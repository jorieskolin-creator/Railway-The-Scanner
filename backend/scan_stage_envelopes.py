"""Typed, references-only boundaries for the wrapped durable scan stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from model_policy import MODEL_POLICY_VERSION
from scan_checkpoint_integrity import validate_checkpoint_integrity
from scan_checkpoints import build_material_ingestion_checkpoint
from scan_diagnosis_checkpoint import build_diagnosis_input_projection
from scan_foresight_checkpoint import (
    DEFAULT_FORESIGHT_KB_VERSION,
    DEFAULT_FORESIGHT_REGISTRY_HASH,
    build_foresight_input_projection,
)
from scan_intent_checkpoint import build_intent_input_projection
from scan_jury_checkpoint import build_jury_input_projection
from scan_supervisor import (
    CheckpointIntegrityError,
    DiagnosisInputSnapshotIntegrityError,
    ForesightInputSnapshotIntegrityError,
    GateResultIntegrityError,
    IntentInputSnapshotIntegrityError,
    JuryInputSnapshotIntegrityError,
    JurySourceCandidateIntegrityError,
    MaterialIntegrityError,
    PersistedGateResult,
    ScanCheckpoint,
    ScanRepository,
    SourceRetrievalIntegrityError,
    StageExecutionResult,
    StageExecutor,
    StepLease,
    SynthesisInputSnapshotIntegrityError,
    VerificationInputSnapshotIntegrityError,
)
from scan_synthesis_checkpoint import build_synthesis_input_projection
from scan_verification_checkpoint import build_verification_input_projection

STAGE_ENVELOPE_CONTRACT = "scanner.stage/v1"
GATE_VERSION = "v1"
_PROJECTIONS = {
    "intent": ("load_verified_intent_inputs", build_intent_input_projection, ("materials", "retrievals")),
    "jury_analysis": ("load_verified_jury_inputs", build_jury_input_projection, ("intent_checkpoint", "candidates", "retrievals")),
    "diagnosis": ("load_verified_diagnosis_inputs", build_diagnosis_input_projection, ("jury_checkpoint",)),
    "foresight": ("load_verified_foresight_inputs", build_foresight_input_projection, ("diagnosis_checkpoint",)),
    "strategic_synthesis": ("load_verified_synthesis_inputs", build_synthesis_input_projection, ("foresight_checkpoint",)),
    "verification": ("load_verified_verification_inputs", build_verification_input_projection, ("synthesis_checkpoint",)),
}
_STAGE_ORDER = ("material_ingestion", *_PROJECTIONS)
_UPSTREAM = dict(zip(_STAGE_ORDER[2:], _STAGE_ORDER[1:]))
_UPSTREAM_REF = {stage: f"{spec[2][0]}_ref" for stage, spec in _PROJECTIONS.items() if stage in _UPSTREAM}
_POLICY_STAGES = frozenset(_STAGE_ORDER[2:])
_STATUSES = frozenset({"passed", "failed", "blocked", "insufficient_evidence", "validation_failed", "approval_required"})


class StageEnvelopeValidationError(ValueError):
    """Invalid typed envelope or contradictory stage transition."""


_INPUT_ERRORS = (
    CheckpointIntegrityError, MaterialIntegrityError, SourceRetrievalIntegrityError,
    JurySourceCandidateIntegrityError, IntentInputSnapshotIntegrityError,
    JuryInputSnapshotIntegrityError, DiagnosisInputSnapshotIntegrityError,
    ForesightInputSnapshotIntegrityError, SynthesisInputSnapshotIntegrityError,
    VerificationInputSnapshotIntegrityError, GateResultIntegrityError,
    StageEnvelopeValidationError,
)
_INPUT_RELOAD_ERRORS = (KeyError, *_INPUT_ERRORS)


@dataclass(frozen=True)
class StageRef:
    id: str
    sha256: str
    kind: str


@dataclass(frozen=True)
class StageGate:
    gate: str
    status: str
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageEnvelope:
    run_id: str
    stage: str
    attempt: int
    input_hash: str | None
    input_refs: tuple[StageRef, ...]
    gate_results: tuple[StageGate, ...]
    started_at: str
    output_hash: str | None = None
    output_refs: tuple[StageRef, ...] = ()
    completed_at: str | None = None
    model_policy_version: str | None = None
    kb_version: str | None = None
    kb_registry_hash: str | None = None
    contract: str = STAGE_ENVELOPE_CONTRACT

    def to_dict(self) -> dict[str, Any]:
        return json.loads(_encode(asdict(self)))


@dataclass(frozen=True)
class StageBindings:
    """Expected versions supplied by the caller, not inferred from stage output."""

    model_policy_version: str = MODEL_POLICY_VERSION
    kb_version: str = DEFAULT_FORESIGHT_KB_VERSION
    kb_registry_hash: str = DEFAULT_FORESIGHT_REGISTRY_HASH


@dataclass(frozen=True)
class StageTransitionOutcome:
    envelope: StageEnvelope
    checkpoint: ScanCheckpoint | None


def _encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_encode(value).encode()).hexdigest()


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value and "\x00" not in value


def _code(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", value) is not None


def _timestamp(value: datetime) -> str:
    if value.utcoffset() is None:
        raise StageEnvelopeValidationError("Stage timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat()


def validate_stage_envelope(value: Mapping[str, Any]) -> StageEnvelope:
    """Parse an exact JSON shape without coercing malformed fields."""

    def refs(items):
        if not isinstance(items, list):
            raise ValueError
        parsed = tuple(StageRef(**item) for item in items)
        if any(not _text(r.id) or not _text(r.kind) or not _digest(r.sha256) for r in parsed):
            raise ValueError
        if len({(r.kind, r.id) for r in parsed}) != len(parsed):
            raise ValueError
        return parsed

    def gate(item):
        if set(item) != {"gate", "status", "reasons", "warnings"}:
            raise ValueError
        for key in ("reasons", "warnings"):
            if not isinstance(item[key], list) or any(not _code(code) for code in item[key]):
                raise ValueError
        if item["status"] not in _STATUSES or bool(item["reasons"]) == (item["status"] == "passed"):
            raise ValueError
        return StageGate(item["gate"], item["status"], tuple(item["reasons"]), tuple(item["warnings"]))

    try:
        if set(value) != set(StageEnvelope.__dataclass_fields__):
            raise ValueError
        if not isinstance(value["gate_results"], list):
            raise ValueError
        envelope = StageEnvelope(**{
            **value,
            "input_refs": refs(value["input_refs"]), "output_refs": refs(value["output_refs"]),
            "gate_results": tuple(gate(item) for item in value["gate_results"]),
        })
        if (envelope.contract != STAGE_ENVELOPE_CONTRACT or not _text(envelope.run_id)
                or envelope.stage not in _STAGE_ORDER or type(envelope.attempt) is not int
                or envelope.attempt < 1):
            raise ValueError
        if len(envelope.gate_results) != 1:
            raise ValueError
        verdict = envelope.gate_results[0]
        start = datetime.fromisoformat(envelope.started_at)
        if _timestamp(start) != envelope.started_at:
            raise ValueError
        egress = envelope.completed_at is not None
        if verdict.gate != ("stage_egress" if egress else "stage_ingress"):
            raise ValueError
        if egress:
            end = datetime.fromisoformat(envelope.completed_at)
            if _timestamp(end) != envelope.completed_at or end < start:
                raise ValueError
        passed = verdict.status == "passed"
        if (envelope.input_hash is not None and not _digest(envelope.input_hash)
                or (envelope.input_hash is None and (passed or egress or envelope.input_refs))
                or (egress and passed and (not _digest(envelope.output_hash) or not envelope.output_refs))
                or (not (egress and passed) and (envelope.output_hash is not None or envelope.output_refs))):
            raise ValueError
        policy = envelope.model_policy_version
        if (envelope.stage in _POLICY_STAGES and not _text(policy)
                or envelope.stage not in _POLICY_STAGES and policy is not None):
            raise ValueError
        if envelope.stage == "foresight":
            if not _text(envelope.kb_version) or not _digest(envelope.kb_registry_hash):
                raise ValueError
        elif envelope.kb_version is not None or envelope.kb_registry_hash is not None:
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError) as error:
        raise StageEnvelopeValidationError("Invalid scanner.stage/v1 envelope.") from error
    return envelope


def _record_payload(envelope: StageEnvelope, checked_at: datetime) -> dict[str, Any]:
    return {"envelope": envelope.to_dict(), "gate_version": GATE_VERSION, "checked_at": _timestamp(checked_at)}


def _envelope_semantics(envelope: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in envelope.items() if key not in {"started_at", "completed_at"}}


def build_persisted_gate_result(envelope: StageEnvelope, *, checked_at: datetime) -> PersistedGateResult:
    envelope = validate_stage_envelope(envelope.to_dict())
    gate = envelope.gate_results[0]
    payload = _record_payload(envelope, checked_at)
    if payload["checked_at"] != (envelope.completed_at or envelope.started_at):
        raise StageEnvelopeValidationError("Gate timestamp does not match its boundary.")
    semantic_hash = _hash({"version": GATE_VERSION, "envelope": _envelope_semantics(payload["envelope"])})
    return PersistedGateResult(
        gate_result_id="GR-" + semantic_hash, run_id=envelope.run_id,
        stage_name=envelope.stage, attempt=envelope.attempt, gate_name=gate.gate,
        gate_version=GATE_VERSION, envelope_contract=envelope.contract,
        input_hash=envelope.input_hash, output_hash=envelope.output_hash,
        input_refs=tuple(asdict(ref) for ref in envelope.input_refs), status=gate.status,
        blocking_reasons=tuple({"code": code} for code in gate.reasons),
        warnings=tuple({"code": code} for code in gate.warnings),
        envelope=payload["envelope"], record_sha256=_hash(payload), checked_at=checked_at,
    )


def validate_persisted_gate_result(record: PersistedGateResult) -> None:
    try:
        expected = build_persisted_gate_result(validate_stage_envelope(record.envelope), checked_at=record.checked_at)
    except (TypeError, ValueError, AttributeError) as error:
        raise GateResultIntegrityError("Persisted gate result envelope is invalid.") from error
    if record != expected:
        raise GateResultIntegrityError("Persisted gate result does not match its hash or envelope.")


def _next_stage(stage: str) -> str | None:
    index = _STAGE_ORDER.index(stage) + 1
    return _STAGE_ORDER[index] if index < len(_STAGE_ORDER) else None


def _output_refs(checkpoint: ScanCheckpoint) -> tuple[StageRef, ...]:
    if checkpoint.stage_name == "material_ingestion":
        return (StageRef(checkpoint.checkpoint_id, checkpoint.output_hash, "material_checkpoint"),)
    return tuple(StageRef(**ref) for ref in checkpoint.snapshot["output_refs"])


def validate_passed_transition(checkpoint: ScanCheckpoint, records: list[PersistedGateResult], next_stage: str | None) -> None:
    """Repository-level check: a valid gate from another output cannot advance a step."""
    by_name = {record.gate_name: record for record in records}
    if set(by_name) != {"stage_ingress", "stage_egress"} or next_stage != _next_stage(checkpoint.stage_name):
        raise StageEnvelopeValidationError("Completion requires both boundary gates and the expected successor.")
    ingress = validate_stage_envelope(by_name["stage_ingress"].envelope)
    egress = validate_stage_envelope(by_name["stage_egress"].envelope)
    if (not _same_input(ingress, egress)
            or ingress.input_hash != checkpoint.input_hash
            or ingress.input_refs != _checkpoint_input_refs(checkpoint)
            or egress.output_hash != checkpoint.output_hash
            or egress.output_refs != _output_refs(checkpoint)
            or any(getattr(egress, key) != checkpoint.snapshot.get(key)
                   for key in ("model_policy_version", "kb_version", "kb_registry_hash"))):
        raise StageEnvelopeValidationError("Boundary gates do not bind the same checkpoint and input.")


def _checkpoint_input_refs(checkpoint: ScanCheckpoint) -> tuple[StageRef, ...]:
    snapshot = checkpoint.snapshot
    if checkpoint.stage_name == "material_ingestion":
        return tuple(StageRef(ref["material_id"], ref["extracted_text_sha256"], "material")
                     for ref in snapshot["material_refs"])
    snapshot_id = snapshot["input_snapshot_ref"].rsplit("/", 1)[-1]
    refs = (StageRef(snapshot_id, checkpoint.input_hash, "input_snapshot"),)
    if checkpoint.stage_name in _UPSTREAM_REF:
        upstream = snapshot[_UPSTREAM_REF[checkpoint.stage_name]]
        refs += (StageRef(upstream["checkpoint_id"], upstream["output_hash"], "upstream_checkpoint"),)
    return refs


def _same_input(ingress: StageEnvelope, egress: StageEnvelope) -> bool:
    return replace(egress, output_hash=None, output_refs=(), completed_at=None,
                   gate_results=ingress.gate_results) == ingress


def validate_blocked_transition(records: list[PersistedGateResult]) -> PersistedGateResult:
    by_name = {record.gate_name: record for record in records}
    ingress = by_name.get("stage_ingress")
    egress = by_name.get("stage_egress")
    if ingress is not None:
        if egress is None and ingress.status != "passed":
            return ingress
        if (egress is not None and ingress.status == "passed" and egress.status != "passed"
                and _same_input(validate_stage_envelope(ingress.envelope), validate_stage_envelope(egress.envelope))):
            return egress
    raise StageEnvelopeValidationError("Blocked transition requires a failed ingress or egress with matching passed ingress.")


def _verify_checkpoint_bindings(repository: ScanRepository, upstream: ScanCheckpoint, bindings: StageBindings) -> bool:
    """Follow actual upstream refs, including the KB binding on later stages."""
    while True:
        validate_checkpoint_integrity(upstream)
        if upstream.stage_name in _POLICY_STAGES and upstream.snapshot["model_policy_version"] != bindings.model_policy_version:
            return False
        if upstream.stage_name == "foresight" and (
            upstream.snapshot["kb_version"], upstream.snapshot["kb_registry_hash"]
        ) != (bindings.kb_version, bindings.kb_registry_hash):
            return False
        records = repository.load_gate_results(
            run_id=upstream.run_id, stage_name=upstream.stage_name, attempt=upstream.attempt,
        )
        if any(record.status != "passed" for record in records):
            raise GateResultIntegrityError("A required upstream gate did not pass.")
        validate_passed_transition(upstream, records, _next_stage(upstream.stage_name))
        previous = _UPSTREAM.get(upstream.stage_name)
        if previous is None:
            return True
        ref = upstream.snapshot[_UPSTREAM_REF[upstream.stage_name]]
        linked = repository.load_checkpoint(run_id=upstream.run_id, stage_name=previous, attempt=ref["attempt"])
        if linked is None:
            raise KeyError("Missing upstream checkpoint.")
        if (linked.checkpoint_id, linked.input_hash, linked.output_hash) != (
            ref["checkpoint_id"], ref["input_hash"], ref["output_hash"]
        ):
            raise CheckpointIntegrityError("Upstream checkpoint reference mismatch.")
        upstream = linked


def run_gated_stage_step(
    repository: ScanRepository, lease: StepLease, executor: StageExecutor,
    *, bindings: StageBindings = StageBindings(), now: datetime | None = None,
) -> StageTransitionOutcome:
    """Validate one leased stage and atomically commit or block; never claim work.

    The caller binds the durable input snapshot first. Provider and unexpected
    operational errors propagate so they are not mislabeled as evidence failures.
    """
    start = now or datetime.now(timezone.utc)
    stage = lease.stage_name
    if stage not in _STAGE_ORDER:
        raise StageEnvelopeValidationError("Unsupported wrapped stage.")
    ingress = StageEnvelope(
        lease.run_id, stage, lease.attempt, None, (), (StageGate("stage_ingress", "passed"),), _timestamp(start),
        model_policy_version=bindings.model_policy_version if stage in _POLICY_STAGES else None,
        kb_version=bindings.kb_version if stage == "foresight" else None,
        kb_registry_hash=bindings.kb_registry_hash if stage == "foresight" else None,
    )

    def block(envelope: StageEnvelope, status: str, reason: str, checked_at: datetime, prior=()):
        failed = replace(envelope, gate_results=(StageGate(envelope.gate_results[0].gate, status, (reason,)),))
        record = build_persisted_gate_result(failed, checked_at=checked_at)
        repository.block_step_with_gates(lease, gate_results=(*prior, record), now=now)
        return StageTransitionOutcome(failed, None)

    expected_fields: dict[str, Any] = {}
    try:
        if stage == "material_ingestion":
            expected = build_material_ingestion_checkpoint(
                run_id=lease.run_id, attempt=lease.attempt, materials=repository.load_materials(run_id=lease.run_id),
            )
            refs = _checkpoint_input_refs(expected)
            input_hash = expected.input_hash
            expected_fields = {"material_refs": expected.snapshot["material_refs"]}
            if expected.gate_result["status"] != "passed":
                ingress = replace(ingress, input_hash=input_hash, input_refs=refs)
                return block(ingress, "blocked", "material_extraction_failed", start)
            upstream = None
        else:
            loader, project, keys = _PROJECTIONS[stage]
            snapshot, *inputs = getattr(repository, loader)(run_id=lease.run_id, attempt=lease.attempt)
            projection = project(snapshot, **dict(zip(keys, inputs, strict=True)))
            if (projection.run_id, projection.stage, projection.attempt) != (lease.run_id, stage, lease.attempt):
                raise CheckpointIntegrityError("Input projection identity mismatch.")
            input_hash = projection.input_hash
            refs = (StageRef(projection.snapshot_id, input_hash, "input_snapshot"),)
            expected_fields = {
                "input_snapshot_ref": projection.snapshot_ref, "input_refs": list(projection.input_refs),
                "payload_sha256": projection.payload_sha256,
            }
            upstream = inputs[0] if stage in _UPSTREAM else None
            if upstream is not None:
                key = f"{keys[0]}_ref"
                expected_fields[key] = getattr(projection, key)
                refs += (StageRef(upstream.checkpoint_id, upstream.output_hash, "upstream_checkpoint"),)
        ingress = replace(ingress, input_hash=input_hash, input_refs=refs)
        if upstream is not None and not _verify_checkpoint_bindings(repository, upstream, bindings):
            return block(ingress, "blocked", "stale_version_binding", start)
    except KeyError:
        return block(ingress, "blocked", "missing_durable_input", start)
    except _INPUT_ERRORS:
        return block(ingress, "blocked", "durable_input_integrity_failed", start)
    ingress_record = build_persisted_gate_result(ingress, checked_at=start)

    # An adapter can reload inputs. If they changed since ingress, never accept its output.
    try:
        result = executor.execute(lease)
    except _INPUT_RELOAD_ERRORS:
        end = now or datetime.now(timezone.utc)
        envelope = replace(ingress, completed_at=_timestamp(end), gate_results=(StageGate("stage_egress", "passed"),))
        return block(envelope, "blocked", "durable_input_changed", end, (ingress_record,))
    end = now or datetime.now(timezone.utc)
    egress = replace(ingress, completed_at=_timestamp(end), gate_results=(StageGate("stage_egress", "passed"),))

    def reject(status: str, reason: str) -> StageTransitionOutcome:
        return block(egress, status, reason, end, (ingress_record,))

    if not isinstance(result, StageExecutionResult):
        return reject("validation_failed", "invalid_stage_result")
    checkpoint = result.checkpoint
    try:
        validate_checkpoint_integrity(checkpoint, require_passed=False)
    except (CheckpointIntegrityError, TypeError, ValueError, AttributeError):
        return reject("validation_failed", "invalid_checkpoint")
    if ((checkpoint.run_id, checkpoint.stage_name, checkpoint.attempt, checkpoint.input_hash)
            != (lease.run_id, stage, lease.attempt, ingress.input_hash)
            or any(checkpoint.snapshot.get(key) != value for key, value in expected_fields.items())):
        return reject("validation_failed", "checkpoint_input_mismatch")
    status = checkpoint.gate_result["status"]
    successor = _next_stage(stage) if status == "passed" else None
    if (result.gate_result != checkpoint.gate_result or result.next_stage != successor
            or result.output_hash != checkpoint.output_hash or result.checkpoint_ref != checkpoint.checkpoint_ref):
        return reject("validation_failed", "contradictory_stage_result")
    if any(getattr(ingress, field) != checkpoint.snapshot.get(field)
           for field in ("model_policy_version", "kb_version", "kb_registry_hash")):
        return reject("blocked", "stale_version_binding")
    warnings = tuple(item.get("code") for item in checkpoint.gate_result["warnings"])
    if not all(_code(code) for code in warnings):
        return reject("validation_failed", "invalid_gate_warnings")
    if status != "passed":
        reasons = tuple(item.get("code") for item in checkpoint.gate_result["reasons"])
        if not reasons or not all(_code(code) for code in reasons):
            return reject("validation_failed", "invalid_gate_reasons")
        # Persist only stable reason codes, not arbitrary fields from diagnostics.
        failed = replace(egress, gate_results=(StageGate("stage_egress", status, reasons, warnings),))
        record = build_persisted_gate_result(failed, checked_at=end)
        repository.block_step_with_gates(lease, gate_results=(ingress_record, record), now=now)
        return StageTransitionOutcome(failed, None)
    egress = replace(
        egress, output_hash=checkpoint.output_hash, output_refs=_output_refs(checkpoint),
        gate_results=(StageGate("stage_egress", "passed", warnings=warnings),),
    )
    record = build_persisted_gate_result(egress, checked_at=end)
    repository.complete_step_with_checkpoint_and_gates(
        lease, checkpoint=checkpoint, gate_results=(ingress_record, record), next_stage=successor, now=now,
    )
    return StageTransitionOutcome(egress, checkpoint)
