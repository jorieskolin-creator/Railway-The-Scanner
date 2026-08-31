"""Versioned Diagnosis input projection and output checkpoint contract.

These builders bind a verified Diagnosis input snapshot to the compact Jury
result a later adapter may read, and to a separately versioned Diagnosis
checkpoint proposal. They do not invoke Diagnosis generation, persist state,
or grant evidence, eligibility, or publication meaning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from scan_diagnosis_snapshot import DIAGNOSIS_STAGE, assert_diagnosis_input_snapshot_matches
from scan_jury_candidates import JURY_ANALYSIS_STAGE
from scan_supervisor import (
    DiagnosisInputSnapshot,
    DiagnosisInputSnapshotIntegrityError,
    ScanCheckpoint,
)


DIAGNOSIS_PROJECTION_CONTRACT = "scanner.diagnosis-projection/v1"
DIAGNOSIS_CHECKPOINT_CONTRACT = "scanner.diagnosis/v1"
DIAGNOSIS_ARCHETYPES = ("Thriving", "Modernizing", "Cash Cow", "At Risk")
_DIAGNOSIS_RESULT_KEYS = {
    "diagnostic_summary",
    "evidence_based_risks",
    "reality_score",
    "archetype",
}


@dataclass(frozen=True)
class DiagnosisInputProjection:
    """Exact adapter-facing view of one verified Diagnosis input snapshot.

    `jury_result` is the only payload a later adapter may use as Diagnosis
    input. It is not stored on the Diagnosis checkpoint.
    """

    contract: str
    run_id: str
    stage: str
    attempt: int
    snapshot_id: str
    snapshot_ref: str
    input_hash: str
    input_refs: tuple[Mapping[str, str], ...]
    jury_checkpoint_ref: Mapping[str, object]
    payload_sha256: str
    jury_result: Mapping[str, Any]


def build_diagnosis_input_projection(
    snapshot: DiagnosisInputSnapshot,
    *,
    jury_checkpoint: ScanCheckpoint,
) -> DiagnosisInputProjection:
    """Project a verified Jury checkpoint into a versioned Diagnosis envelope payload."""

    try:
        assert_diagnosis_input_snapshot_matches(
            snapshot,
            jury_checkpoint=jury_checkpoint,
        )
        jury_result = _jury_result_from_checkpoint(jury_checkpoint)
    except DiagnosisInputSnapshotIntegrityError:
        raise
    except (TypeError, ValueError) as error:
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input projection cannot be built from durable inputs."
        ) from error

    input_refs = (
        {
            "id": jury_checkpoint.checkpoint_id,
            "sha256": jury_checkpoint.output_hash,
            "kind": "jury_checkpoint",
        },
    )
    return DiagnosisInputProjection(
        contract=DIAGNOSIS_PROJECTION_CONTRACT,
        run_id=snapshot.run_id,
        stage=DIAGNOSIS_STAGE,
        attempt=snapshot.attempt,
        snapshot_id=snapshot.snapshot_id,
        snapshot_ref=snapshot.snapshot_ref,
        input_hash=snapshot.input_hash,
        input_refs=input_refs,
        jury_checkpoint_ref=dict(snapshot.manifest["jury_checkpoint_ref"]),
        payload_sha256=_hash(jury_result),
        jury_result=jury_result,
    )


def build_diagnosis_checkpoint(
    projection: DiagnosisInputProjection,
    *,
    diagnosis_result: Mapping[str, Any] | object,
) -> ScanCheckpoint:
    """Build a Diagnosis output checkpoint from a projection and a supplied result.

    The caller supplies the Diagnosis result mapping. This function never
    calls a model and never writes a database row.
    """

    _validate_projection(projection)
    compact_result = _compact_diagnosis_result(diagnosis_result)
    if compact_result is None:
        gate_result = {
            "gate": DIAGNOSIS_CHECKPOINT_CONTRACT,
            "status": "validation_failed",
            "reasons": [{"code": "malformed_diagnosis_result"}],
            "warnings": [],
        }
        output_refs: list[dict[str, str]] = []
        diagnosis_payload: Mapping[str, Any] | None = None
    else:
        gate_result = {
            "gate": DIAGNOSIS_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        }
        output_payload_hash = _hash(compact_result)
        output_refs = [
            {
                "id": "DRO-"
                + _hash(
                    {
                        "run_id": projection.run_id,
                        "stage": DIAGNOSIS_STAGE,
                        "attempt": projection.attempt,
                        "sha256": output_payload_hash,
                    }
                )[:32].upper(),
                "sha256": output_payload_hash,
                "kind": "diagnosis_result",
            }
        ]
        diagnosis_payload = compact_result

    snapshot: dict[str, Any] = {
        "contract": DIAGNOSIS_CHECKPOINT_CONTRACT,
        "run_id": projection.run_id,
        "stage": DIAGNOSIS_STAGE,
        "attempt": projection.attempt,
        "input_hash": projection.input_hash,
        "input_refs": [dict(reference) for reference in projection.input_refs],
        "output_refs": output_refs,
        "gate_results": [gate_result],
        "input_snapshot_ref": projection.snapshot_ref,
        "jury_checkpoint_ref": dict(projection.jury_checkpoint_ref),
        "payload_sha256": projection.payload_sha256,
    }
    if diagnosis_payload is not None:
        snapshot["diagnosis_result"] = diagnosis_payload
    output_hash = _hash(snapshot)
    checkpoint_id = "CP-" + _hash(
        {
            "run_id": projection.run_id,
            "stage": DIAGNOSIS_STAGE,
            "attempt": projection.attempt,
            "contract": DIAGNOSIS_CHECKPOINT_CONTRACT,
            "output_hash": output_hash,
        }
    )[:32].upper()
    return ScanCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=projection.run_id,
        stage_name=DIAGNOSIS_STAGE,
        attempt=projection.attempt,
        contract_version=DIAGNOSIS_CHECKPOINT_CONTRACT,
        input_hash=projection.input_hash,
        output_hash=output_hash,
        gate_result=gate_result,
        snapshot=snapshot,
    )


def empty_diagnosis_result() -> dict[str, Any]:
    """Valid empty Diagnosis payload used by the prepare-only default wrap."""

    return {
        "diagnostic_summary": "",
        "evidence_based_risks": [],
        "reality_score": 0,
        "archetype": "At Risk",
    }


def _validate_projection(projection: DiagnosisInputProjection) -> None:
    if projection.contract != DIAGNOSIS_PROJECTION_CONTRACT:
        raise DiagnosisInputSnapshotIntegrityError("Diagnosis projection contract is invalid.")
    if (
        not projection.run_id
        or projection.stage != DIAGNOSIS_STAGE
        or projection.attempt < 1
        or not projection.snapshot_id
        or not projection.input_hash
    ):
        raise DiagnosisInputSnapshotIntegrityError("Diagnosis projection identity is invalid.")
    if _hash(dict(projection.jury_result)) != projection.payload_sha256:
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis projection payload hash does not match Jury result."
        )
    if (
        not projection.input_refs
        or projection.input_refs[0].get("kind") != "jury_checkpoint"
        or projection.input_refs[0].get("id") != projection.jury_checkpoint_ref.get("checkpoint_id")
    ):
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis projection does not bind the Jury checkpoint."
        )


def _jury_result_from_checkpoint(checkpoint: ScanCheckpoint) -> dict[str, Any]:
    if checkpoint.stage_name != JURY_ANALYSIS_STAGE:
        raise ValueError("Diagnosis input requires a passed Jury checkpoint.")
    jury_result = checkpoint.snapshot.get("jury_result")
    if not isinstance(jury_result, Mapping):
        raise ValueError("Diagnosis input requires a compact Jury result.")
    compact = _json_safe_mapping(jury_result)
    if compact is None:
        raise ValueError("Diagnosis input Jury result must be JSON-safe.")
    return compact


def _compact_diagnosis_result(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != _DIAGNOSIS_RESULT_KEYS:
        return None
    summary = value.get("diagnostic_summary")
    risks = value.get("evidence_based_risks")
    score = value.get("reality_score")
    archetype = value.get("archetype")
    if not isinstance(summary, str):
        return None
    if not isinstance(risks, list) or any(not isinstance(item, str) for item in risks):
        return None
    if type(score) is not int or score < 0 or score > 100:
        return None
    if archetype not in DIAGNOSIS_ARCHETYPES:
        return None
    return {
        "diagnostic_summary": summary,
        "evidence_based_risks": list(risks),
        "reality_score": score,
        "archetype": archetype,
    }


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        return json.loads(
            json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
    except (TypeError, ValueError):
        return None


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
