"""Deterministic reload integrity verification for persisted ScanCheckpoints.

Checkpoints are operational stage lineage only. Reloading a checkpoint must
never trust stored hash columns blindly: this module recomputes canonical
snapshot/output hashes, deterministic checkpoint IDs, input-snapshot bindings,
embedded gate verdicts, and compact stage output references so any JSON payload
mutation fails closed with `CheckpointIntegrityError`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from scan_checkpoints import (
    MATERIAL_INGESTION_CHECKPOINT_CONTRACT,
    MATERIAL_INGESTION_STAGE,
)
from scan_diagnosis_checkpoint import (
    DIAGNOSIS_CHECKPOINT_CONTRACT,
    _compact_diagnosis_result,
)
from scan_diagnosis_snapshot import (
    DIAGNOSIS_INPUT_SNAPSHOT_CONTRACT,
    DIAGNOSIS_STAGE,
)
from scan_foresight_checkpoint import (
    FORESIGHT_CHECKPOINT_CONTRACT,
    _compact_foresight_result,
)
from scan_foresight_snapshot import (
    FORESIGHT_INPUT_SNAPSHOT_CONTRACT,
    FORESIGHT_STAGE,
)
from scan_intent_checkpoint import (
    INTENT_CHECKPOINT_CONTRACT,
    _compact_intent_result,
)
from scan_intent_snapshot import (
    INTENT_INPUT_SNAPSHOT_CONTRACT,
    INTENT_STAGE,
)
from scan_jury_candidates import JURY_ANALYSIS_STAGE
from scan_jury_checkpoint import (
    JURY_CHECKPOINT_CONTRACT,
    _compact_jury_result,
)
from scan_jury_snapshot import JURY_INPUT_SNAPSHOT_CONTRACT
from scan_supervisor import CheckpointIntegrityError, ScanCheckpoint
from scan_synthesis_checkpoint import (
    SYNTHESIS_CHECKPOINT_CONTRACT,
    _compact_synthesis_result,
)
from scan_synthesis_snapshot import (
    SYNTHESIS_INPUT_SNAPSHOT_CONTRACT,
    SYNTHESIS_STAGE,
)
from scan_verification_checkpoint import (
    VERIFICATION_CHECKPOINT_CONTRACT,
    _compact_verification_result,
)
from scan_verification_snapshot import (
    VERIFICATION_INPUT_SNAPSHOT_CONTRACT,
    VERIFICATION_STAGE,
)


_GATE_RESULT_KEYS = frozenset({"gate", "status", "reasons", "warnings"})
_ALLOWED_GATE_STATUSES = frozenset(
    {
        "passed",
        "failed",
        "blocked",
        "insufficient_evidence",
        "validation_failed",
        "approval_required",
    }
)
_MATERIAL_REF_KEYS = frozenset(
    {
        "material_id",
        "origin",
        "filename",
        "media_type",
        "byte_size",
        "content_sha256",
        "extracted_text_sha256",
        "parser_version",
        "extraction_status",
        "allowed_use_classification",
    }
)
_ALLOWED_MATERIAL_ORIGINS = frozenset(
    {"submitted_report_text", "uploaded_file", "source_retrieval"}
)
_ALLOWED_MATERIAL_EXTRACTION_STATUSES = frozenset({"extracted", "empty", "failed"})
_REF_ITEM_KEYS = frozenset({"id", "sha256", "kind"})
_UPSTREAM_CHECKPOINT_REF_KEYS = frozenset(
    {
        "checkpoint_id",
        "checkpoint_ref",
        "stage",
        "attempt",
        "contract_version",
        "input_hash",
        "output_hash",
    }
)


@dataclass(frozen=True)
class _CognitiveCheckpointSpec:
    stage_name: str
    contract_version: str
    input_snapshot_contract: str
    input_snapshot_id_prefix: str
    input_snapshot_scheme: str
    allowed_input_ref_kinds: tuple[str, ...]
    result_key: str
    output_ref_prefix: str
    output_ref_kind: str
    compact_validator: Callable[[object], dict[str, Any] | None]
    malformed_reason_code: str
    upstream_ref_key: str | None = None
    upstream_stage: str | None = None
    upstream_contract: str | None = None
    binds_single_upstream_input_ref: bool = False


_COGNITIVE_SPECS: Mapping[str, _CognitiveCheckpointSpec] = {
    INTENT_STAGE: _CognitiveCheckpointSpec(
        stage_name=INTENT_STAGE,
        contract_version=INTENT_CHECKPOINT_CONTRACT,
        input_snapshot_contract=INTENT_INPUT_SNAPSHOT_CONTRACT,
        input_snapshot_id_prefix="IIS",
        input_snapshot_scheme="intent-input://scan/",
        allowed_input_ref_kinds=("material", "source_retrieval"),
        result_key="intent_result",
        output_ref_prefix="IRO",
        output_ref_kind="intent_result",
        compact_validator=_compact_intent_result,
        malformed_reason_code="malformed_intent_result",
    ),
    JURY_ANALYSIS_STAGE: _CognitiveCheckpointSpec(
        stage_name=JURY_ANALYSIS_STAGE,
        contract_version=JURY_CHECKPOINT_CONTRACT,
        input_snapshot_contract=JURY_INPUT_SNAPSHOT_CONTRACT,
        input_snapshot_id_prefix="JIS",
        input_snapshot_scheme="jury-input://scan/",
        allowed_input_ref_kinds=("jury_source_candidate", "source_retrieval"),
        result_key="jury_result",
        output_ref_prefix="JRO",
        output_ref_kind="jury_result",
        compact_validator=_compact_jury_result,
        malformed_reason_code="malformed_jury_result",
        upstream_ref_key="intent_checkpoint_ref",
        upstream_stage=INTENT_STAGE,
        upstream_contract=INTENT_CHECKPOINT_CONTRACT,
        binds_single_upstream_input_ref=False,
    ),
    DIAGNOSIS_STAGE: _CognitiveCheckpointSpec(
        stage_name=DIAGNOSIS_STAGE,
        contract_version=DIAGNOSIS_CHECKPOINT_CONTRACT,
        input_snapshot_contract=DIAGNOSIS_INPUT_SNAPSHOT_CONTRACT,
        input_snapshot_id_prefix="DIS",
        input_snapshot_scheme="diagnosis-input://scan/",
        allowed_input_ref_kinds=("jury_checkpoint",),
        result_key="diagnosis_result",
        output_ref_prefix="DRO",
        output_ref_kind="diagnosis_result",
        compact_validator=_compact_diagnosis_result,
        malformed_reason_code="malformed_diagnosis_result",
        upstream_ref_key="jury_checkpoint_ref",
        upstream_stage=JURY_ANALYSIS_STAGE,
        upstream_contract=JURY_CHECKPOINT_CONTRACT,
        binds_single_upstream_input_ref=True,
    ),
    FORESIGHT_STAGE: _CognitiveCheckpointSpec(
        stage_name=FORESIGHT_STAGE,
        contract_version=FORESIGHT_CHECKPOINT_CONTRACT,
        input_snapshot_contract=FORESIGHT_INPUT_SNAPSHOT_CONTRACT,
        input_snapshot_id_prefix="FIS",
        input_snapshot_scheme="foresight-input://scan/",
        allowed_input_ref_kinds=("diagnosis_checkpoint",),
        result_key="foresight_result",
        output_ref_prefix="FRO",
        output_ref_kind="foresight_result",
        compact_validator=_compact_foresight_result,
        malformed_reason_code="malformed_foresight_result",
        upstream_ref_key="diagnosis_checkpoint_ref",
        upstream_stage=DIAGNOSIS_STAGE,
        upstream_contract=DIAGNOSIS_CHECKPOINT_CONTRACT,
        binds_single_upstream_input_ref=True,
    ),
    SYNTHESIS_STAGE: _CognitiveCheckpointSpec(
        stage_name=SYNTHESIS_STAGE,
        contract_version=SYNTHESIS_CHECKPOINT_CONTRACT,
        input_snapshot_contract=SYNTHESIS_INPUT_SNAPSHOT_CONTRACT,
        input_snapshot_id_prefix="SIS",
        input_snapshot_scheme="synthesis-input://scan/",
        allowed_input_ref_kinds=("foresight_checkpoint",),
        result_key="synthesis_result",
        output_ref_prefix="SYO",
        output_ref_kind="synthesis_result",
        compact_validator=_compact_synthesis_result,
        malformed_reason_code="malformed_synthesis_result",
        upstream_ref_key="foresight_checkpoint_ref",
        upstream_stage=FORESIGHT_STAGE,
        upstream_contract=FORESIGHT_CHECKPOINT_CONTRACT,
        binds_single_upstream_input_ref=True,
    ),
    VERIFICATION_STAGE: _CognitiveCheckpointSpec(
        stage_name=VERIFICATION_STAGE,
        contract_version=VERIFICATION_CHECKPOINT_CONTRACT,
        input_snapshot_contract=VERIFICATION_INPUT_SNAPSHOT_CONTRACT,
        input_snapshot_id_prefix="VIS",
        input_snapshot_scheme="verification-input://scan/",
        allowed_input_ref_kinds=("synthesis_checkpoint",),
        result_key="verification_result",
        output_ref_prefix="VRO",
        output_ref_kind="verification_result",
        compact_validator=_compact_verification_result,
        malformed_reason_code="malformed_verification_result",
        upstream_ref_key="synthesis_checkpoint_ref",
        upstream_stage=SYNTHESIS_STAGE,
        upstream_contract=SYNTHESIS_CHECKPOINT_CONTRACT,
        binds_single_upstream_input_ref=True,
    ),
}


def validate_checkpoint_integrity(
    checkpoint: ScanCheckpoint,
    *,
    require_passed: bool = True,
) -> None:
    """Verify checkpoint hashes, deterministic ID, embedded gates, and refs."""

    if not isinstance(checkpoint, ScanCheckpoint):
        raise CheckpointIntegrityError("Checkpoint must be a ScanCheckpoint instance.")
    if (
        not _is_non_empty_str(checkpoint.checkpoint_id)
        or not _is_non_empty_str(checkpoint.run_id)
        or not _is_non_empty_str(checkpoint.stage_name)
        or not _is_non_empty_str(checkpoint.contract_version)
    ):
        raise CheckpointIntegrityError("Checkpoint identity fields are required.")
    if type(checkpoint.attempt) is not int or checkpoint.attempt < 1:
        raise CheckpointIntegrityError("Checkpoint attempt must be a positive integer.")
    if not _is_sha256_digest(checkpoint.input_hash) or not _is_sha256_digest(
        checkpoint.output_hash
    ):
        raise CheckpointIntegrityError("Checkpoint input/output hashes must be SHA-256 digests.")
    if not isinstance(checkpoint.gate_result, Mapping) or not isinstance(
        checkpoint.snapshot, Mapping
    ):
        raise CheckpointIntegrityError("Checkpoint gate_result and snapshot must be mappings.")

    gate_result = _canonical_mapping(checkpoint.gate_result, label="gate_result")
    snapshot = _canonical_mapping(checkpoint.snapshot, label="snapshot")

    _validate_gate_envelope(
        gate_result,
        contract_version=checkpoint.contract_version,
        require_passed=require_passed,
    )
    _validate_snapshot_header(checkpoint, snapshot)

    if checkpoint.stage_name == MATERIAL_INGESTION_STAGE:
        _validate_material_ingestion_checkpoint(checkpoint, gate_result, snapshot)
    elif checkpoint.stage_name in _COGNITIVE_SPECS:
        _validate_cognitive_checkpoint(
            checkpoint,
            gate_result,
            snapshot,
            spec=_COGNITIVE_SPECS[checkpoint.stage_name],
        )
    else:
        raise CheckpointIntegrityError(
            f"Unsupported checkpoint stage: {checkpoint.stage_name!r}."
        )

    recomputed_output_hash = _hash(snapshot)
    if recomputed_output_hash != checkpoint.output_hash:
        raise CheckpointIntegrityError(
            f"Checkpoint output_hash mismatch for {checkpoint.run_id}/{checkpoint.stage_name}/{checkpoint.attempt}."
        )

    expected_checkpoint_id = "CP-" + _hash(
        {
            "run_id": checkpoint.run_id,
            "stage": checkpoint.stage_name,
            "attempt": checkpoint.attempt,
            "contract": checkpoint.contract_version,
            "output_hash": recomputed_output_hash,
        }
    )[:32].upper()
    if checkpoint.checkpoint_id != expected_checkpoint_id:
        raise CheckpointIntegrityError(
            f"Checkpoint checkpoint_id mismatch for {checkpoint.run_id}/{checkpoint.stage_name}/{checkpoint.attempt}."
        )


def _validate_gate_envelope(
    gate_result: Mapping[str, Any],
    *,
    contract_version: str,
    require_passed: bool,
) -> None:
    if set(gate_result) != _GATE_RESULT_KEYS:
        raise CheckpointIntegrityError("Checkpoint gate_result has unexpected or missing keys.")
    if gate_result.get("gate") != contract_version:
        raise CheckpointIntegrityError(
            "Checkpoint gate_result gate does not match contract_version."
        )
    status = gate_result.get("status")
    if not isinstance(status, str) or status not in _ALLOWED_GATE_STATUSES:
        raise CheckpointIntegrityError("Checkpoint gate_result status is invalid.")
    reasons = gate_result.get("reasons")
    warnings = gate_result.get("warnings")
    if not isinstance(reasons, list) or any(not isinstance(item, Mapping) for item in reasons):
        raise CheckpointIntegrityError("Checkpoint gate_result reasons must be mappings.")
    if not isinstance(warnings, list) or any(not isinstance(item, Mapping) for item in warnings):
        raise CheckpointIntegrityError("Checkpoint gate_result warnings must be mappings.")
    if require_passed and status != "passed":
        raise CheckpointIntegrityError(
            f"Persisted checkpoint must have status='passed', got {status!r}."
        )
    if status == "passed" and reasons:
        raise CheckpointIntegrityError("Passed checkpoint gate_result cannot carry failure reasons.")


def _validate_snapshot_header(
    checkpoint: ScanCheckpoint,
    snapshot: Mapping[str, Any],
) -> None:
    if (
        snapshot.get("contract") != checkpoint.contract_version
        or snapshot.get("run_id") != checkpoint.run_id
        or snapshot.get("stage") != checkpoint.stage_name
        or type(snapshot.get("attempt")) is not int
        or snapshot.get("attempt") != checkpoint.attempt
        or snapshot.get("input_hash") != checkpoint.input_hash
    ):
        raise CheckpointIntegrityError(
            "Checkpoint snapshot header does not match row identity or input_hash."
        )


def _validate_material_ingestion_checkpoint(
    checkpoint: ScanCheckpoint,
    gate_result: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    if checkpoint.contract_version != MATERIAL_INGESTION_CHECKPOINT_CONTRACT:
        raise CheckpointIntegrityError(
            "Material ingestion checkpoint contract_version is invalid."
        )
    expected_keys = {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "input_hash",
        "material_refs",
        "gate_result",
    }
    if set(snapshot) != expected_keys:
        raise CheckpointIntegrityError(
            "Material ingestion checkpoint snapshot has unexpected or missing keys."
        )
    if snapshot.get("gate_result") != gate_result:
        raise CheckpointIntegrityError(
            "Material ingestion checkpoint embedded gate_result does not match row gate_result."
        )
    material_refs = snapshot.get("material_refs")
    if not isinstance(material_refs, list):
        raise CheckpointIntegrityError(
            "Material ingestion checkpoint material_refs must be a list."
        )
    material_ids: list[str] = []
    for reference in material_refs:
        if not isinstance(reference, Mapping) or set(reference) != _MATERIAL_REF_KEYS:
            raise CheckpointIntegrityError(
                "Material ingestion checkpoint material_ref shape is invalid."
            )
        material_id = reference.get("material_id")
        origin = reference.get("origin")
        filename = reference.get("filename")
        media_type = reference.get("media_type")
        byte_size = reference.get("byte_size")
        content_sha256 = reference.get("content_sha256")
        extracted_text_sha256 = reference.get("extracted_text_sha256")
        parser_version = reference.get("parser_version")
        extraction_status = reference.get("extraction_status")
        allowed_use = reference.get("allowed_use_classification")
        if (
            not _is_non_empty_str(material_id)
            or not _is_non_empty_str(filename)
            or not _is_non_empty_str(media_type)
            or not _is_non_empty_str(parser_version)
            or not _is_non_empty_str(allowed_use)
            or origin not in _ALLOWED_MATERIAL_ORIGINS
            or extraction_status not in _ALLOWED_MATERIAL_EXTRACTION_STATUSES
            or type(byte_size) is not int
            or byte_size < 0
            or not isinstance(content_sha256, str)
            or not _is_sha256_digest(content_sha256)
            or not isinstance(extracted_text_sha256, str)
            or not _is_sha256_digest(extracted_text_sha256)
        ):
            raise CheckpointIntegrityError(
                "Material ingestion checkpoint material_ref fields are invalid."
            )
        material_ids.append(material_id)

    if material_ids != sorted(material_ids) or len(set(material_ids)) != len(material_ids):
        raise CheckpointIntegrityError(
            "Material ingestion checkpoint material_refs must be sorted and unique."
        )

    failed_ids = [
        ref["material_id"]
        for ref in material_refs
        if ref["extraction_status"] == "failed"
    ]
    empty_ids = [
        ref["material_id"]
        for ref in material_refs
        if ref["extraction_status"] == "empty"
    ]
    expected_gate_result = {
        "gate": MATERIAL_INGESTION_CHECKPOINT_CONTRACT,
        "status": "blocked" if failed_ids else "passed",
        "reasons": (
            [{"code": "material_extraction_failed", "material_ids": failed_ids}]
            if failed_ids
            else []
        ),
        "warnings": (
            [{"code": "no_submitted_material"}]
            if not material_refs
            else (
                [{"code": "material_extraction_empty", "material_ids": empty_ids}]
                if empty_ids
                else []
            )
        ),
    }
    if gate_result != expected_gate_result:
        raise CheckpointIntegrityError(
            "Material ingestion checkpoint gate_result does not match material_refs."
        )

    expected_input_hash = _hash(
        {
            "contract": MATERIAL_INGESTION_CHECKPOINT_CONTRACT,
            "run_id": checkpoint.run_id,
            "stage": MATERIAL_INGESTION_STAGE,
            "attempt": checkpoint.attempt,
            "material_refs": material_refs,
        }
    )
    if expected_input_hash != checkpoint.input_hash:
        raise CheckpointIntegrityError(
            "Material ingestion checkpoint input_hash does not match material_refs."
        )


def _validate_cognitive_checkpoint(
    checkpoint: ScanCheckpoint,
    gate_result: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    spec: _CognitiveCheckpointSpec,
) -> None:
    if checkpoint.contract_version != spec.contract_version:
        raise CheckpointIntegrityError(
            f"Checkpoint contract_version {checkpoint.contract_version!r} does not match stage {spec.stage_name!r}."
        )
    expected_keys = {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "input_hash",
        "input_refs",
        "output_refs",
        "gate_results",
        "input_snapshot_ref",
        "payload_sha256",
    }
    if spec.upstream_ref_key is not None:
        expected_keys.add(spec.upstream_ref_key)
    status = gate_result["status"]
    if status == "passed":
        expected_keys.add(spec.result_key)
    if set(snapshot) != expected_keys:
        raise CheckpointIntegrityError(
            f"{spec.stage_name} checkpoint snapshot has unexpected or missing keys."
        )

    gate_results = snapshot.get("gate_results")
    if not isinstance(gate_results, list) or gate_results != [gate_result]:
        raise CheckpointIntegrityError(
            f"{spec.stage_name} checkpoint embedded gate_results do not match row gate_result."
        )

    payload_sha256 = snapshot.get("payload_sha256")
    if not isinstance(payload_sha256, str) or not _is_sha256_digest(payload_sha256):
        raise CheckpointIntegrityError(
            f"{spec.stage_name} checkpoint payload_sha256 must be a SHA-256 digest."
        )

    expected_snapshot_id = f"{spec.input_snapshot_id_prefix}-" + _hash(
        {
            "run_id": checkpoint.run_id,
            "stage": checkpoint.stage_name,
            "attempt": checkpoint.attempt,
            "contract": spec.input_snapshot_contract,
            "input_hash": checkpoint.input_hash,
        }
    )[:32].upper()
    expected_snapshot_ref = f"{spec.input_snapshot_scheme}{expected_snapshot_id}"
    if snapshot.get("input_snapshot_ref") != expected_snapshot_ref:
        raise CheckpointIntegrityError(
            f"{spec.stage_name} checkpoint input_snapshot_ref does not match deterministic input snapshot identity."
        )

    input_refs = _validate_input_refs(
        snapshot.get("input_refs"),
        allowed_kinds=spec.allowed_input_ref_kinds,
        stage_name=spec.stage_name,
    )

    if spec.upstream_ref_key is not None:
        upstream_ref = _validate_upstream_checkpoint_ref(
            snapshot.get(spec.upstream_ref_key),
            expected_stage=spec.upstream_stage or "",
            expected_contract=spec.upstream_contract or "",
            stage_name=spec.stage_name,
        )
        if spec.binds_single_upstream_input_ref:
            expected_input_refs = [
                {
                    "id": upstream_ref["checkpoint_id"],
                    "sha256": upstream_ref["output_hash"],
                    "kind": spec.allowed_input_ref_kinds[0],
                }
            ]
            if input_refs != expected_input_refs:
                raise CheckpointIntegrityError(
                    f"{spec.stage_name} checkpoint input_refs do not match {spec.upstream_ref_key}."
                )
            expected_input_hash = _hash(
                {
                    "contract": spec.input_snapshot_contract,
                    "run_id": checkpoint.run_id,
                    "stage": checkpoint.stage_name,
                    "attempt": checkpoint.attempt,
                    spec.upstream_ref_key: upstream_ref,
                }
            )
            if expected_input_hash != checkpoint.input_hash:
                raise CheckpointIntegrityError(
                    f"{spec.stage_name} checkpoint input_hash does not match {spec.upstream_ref_key}."
                )

    output_refs = snapshot.get("output_refs")
    if not isinstance(output_refs, list):
        raise CheckpointIntegrityError(
            f"{spec.stage_name} checkpoint output_refs must be a list."
        )

    if status == "passed":
        raw_result = snapshot.get(spec.result_key)
        compact_result = spec.compact_validator(raw_result)
        if compact_result is None or compact_result != raw_result:
            raise CheckpointIntegrityError(
                f"{spec.stage_name} checkpoint {spec.result_key} failed compact schema verification."
            )
        expected_result_sha256 = _hash(compact_result)
        expected_output_ref = {
            "id": f"{spec.output_ref_prefix}-"
            + _hash(
                {
                    "run_id": checkpoint.run_id,
                    "stage": checkpoint.stage_name,
                    "attempt": checkpoint.attempt,
                    "sha256": expected_result_sha256,
                }
            )[:32].upper(),
            "sha256": expected_result_sha256,
            "kind": spec.output_ref_kind,
        }
        if output_refs != [expected_output_ref]:
            raise CheckpointIntegrityError(
                f"{spec.stage_name} checkpoint output_refs do not match {spec.result_key} hash."
            )
    else:
        if output_refs != []:
            raise CheckpointIntegrityError(
                f"Non-passed {spec.stage_name} checkpoint cannot carry output_refs."
            )
        if status == "validation_failed" and gate_result.get("reasons") != [
            {"code": spec.malformed_reason_code}
        ]:
            raise CheckpointIntegrityError(
                f"Validation-failed {spec.stage_name} checkpoint has inconsistent gate reasons."
            )


def _validate_input_refs(
    value: object,
    *,
    allowed_kinds: tuple[str, ...],
    stage_name: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise CheckpointIntegrityError(f"{stage_name} checkpoint input_refs must be a list.")
    kind_positions = {kind: idx for idx, kind in enumerate(allowed_kinds)}
    last_kind_idx = -1
    seen_by_kind: dict[str, list[str]] = {kind: [] for kind in allowed_kinds}
    normalized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _REF_ITEM_KEYS:
            raise CheckpointIntegrityError(
                f"{stage_name} checkpoint input_ref shape is invalid."
            )
        ref_id = item.get("id")
        ref_sha256 = item.get("sha256")
        ref_kind = item.get("kind")
        if (
            not _is_non_empty_str(ref_id)
            or not isinstance(ref_sha256, str)
            or not _is_sha256_digest(ref_sha256)
            or not isinstance(ref_kind, str)
            or ref_kind not in kind_positions
        ):
            raise CheckpointIntegrityError(
                f"{stage_name} checkpoint input_ref fields are invalid."
            )
        current_kind_idx = kind_positions[ref_kind]
        if current_kind_idx < last_kind_idx:
            raise CheckpointIntegrityError(
                f"{stage_name} checkpoint input_refs kinds are out of canonical order."
            )
        last_kind_idx = current_kind_idx
        seen_by_kind[ref_kind].append(ref_id)
        normalized.append({"id": ref_id, "sha256": ref_sha256, "kind": ref_kind})

    for kind, ids in seen_by_kind.items():
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise CheckpointIntegrityError(
                f"{stage_name} checkpoint {kind} input_refs must be sorted and unique."
            )
    return normalized


def _validate_upstream_checkpoint_ref(
    value: object,
    *,
    expected_stage: str,
    expected_contract: str,
    stage_name: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _UPSTREAM_CHECKPOINT_REF_KEYS:
        raise CheckpointIntegrityError(
            f"{stage_name} checkpoint upstream checkpoint ref has invalid keys."
        )
    checkpoint_id = value.get("checkpoint_id")
    checkpoint_ref = value.get("checkpoint_ref")
    ref_stage = value.get("stage")
    attempt = value.get("attempt")
    contract_version = value.get("contract_version")
    input_hash = value.get("input_hash")
    output_hash = value.get("output_hash")
    if (
        not _is_non_empty_str(checkpoint_id)
        or checkpoint_ref != f"checkpoint://scan/{checkpoint_id}"
        or ref_stage != expected_stage
        or type(attempt) is not int
        or attempt < 1
        or contract_version != expected_contract
        or not isinstance(input_hash, str)
        or not _is_sha256_digest(input_hash)
        or not isinstance(output_hash, str)
        or not _is_sha256_digest(output_hash)
    ):
        raise CheckpointIntegrityError(
            f"{stage_name} checkpoint upstream checkpoint ref fields are invalid."
        )
    return dict(value)


def _canonical_mapping(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise CheckpointIntegrityError(
            f"Checkpoint {label} must be a finite JSON-safe mapping."
        ) from error
    parsed = json.loads(encoded)
    if not isinstance(parsed, dict):
        raise CheckpointIntegrityError(f"Checkpoint {label} must serialize to a JSON object.")
    return parsed


def _is_non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_sha256_digest(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
