"""Versioned Verification input projection and output checkpoint contract.

These builders bind a verified Verification input snapshot to the compact
Synthesis result a later adapter may read, and to a separately versioned
Verification checkpoint proposal. They do not invoke Verification generation,
persist state, copy Foresight KB content, or grant evidence, eligibility,
or publication meaning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from scan_supervisor import (
    ScanCheckpoint,
    VerificationInputSnapshot,
    VerificationInputSnapshotIntegrityError,
)
from scan_synthesis_checkpoint import SYNTHESIS_CHECKPOINT_CONTRACT
from scan_synthesis_snapshot import SYNTHESIS_STAGE
from scan_verification_snapshot import (
    VERIFICATION_STAGE,
    assert_verification_input_snapshot_matches,
)


VERIFICATION_PROJECTION_CONTRACT = "scanner.verification-projection/v1"
VERIFICATION_CHECKPOINT_CONTRACT = "scanner.verification/v1"
_VERIFICATION_RESULT_KEYS = {
    "verification_record",
}


@dataclass(frozen=True)
class VerificationInputProjection:
    """Exact adapter-facing view of one verified Verification input snapshot.

    `synthesis_result` is the only payload a later adapter may use as
    Verification input. It is not stored on the Verification checkpoint.
    """

    contract: str
    run_id: str
    stage: str
    attempt: int
    snapshot_id: str
    snapshot_ref: str
    input_hash: str
    input_refs: tuple[Mapping[str, str], ...]
    synthesis_checkpoint_ref: Mapping[str, object]
    payload_sha256: str
    synthesis_result: Mapping[str, Any]


def build_verification_input_projection(
    snapshot: VerificationInputSnapshot,
    *,
    synthesis_checkpoint: ScanCheckpoint,
) -> VerificationInputProjection:
    """Project a verified Synthesis checkpoint into a versioned Verification envelope."""

    try:
        assert_verification_input_snapshot_matches(
            snapshot,
            synthesis_checkpoint=synthesis_checkpoint,
        )
        synthesis_result = _synthesis_result_from_checkpoint(synthesis_checkpoint)
    except VerificationInputSnapshotIntegrityError:
        raise
    except (TypeError, ValueError) as error:
        raise VerificationInputSnapshotIntegrityError(
            "Verification input projection cannot be built from durable inputs."
        ) from error

    input_refs = (
        {
            "id": synthesis_checkpoint.checkpoint_id,
            "sha256": synthesis_checkpoint.output_hash,
            "kind": "synthesis_checkpoint",
        },
    )
    return VerificationInputProjection(
        contract=VERIFICATION_PROJECTION_CONTRACT,
        run_id=snapshot.run_id,
        stage=VERIFICATION_STAGE,
        attempt=snapshot.attempt,
        snapshot_id=snapshot.snapshot_id,
        snapshot_ref=snapshot.snapshot_ref,
        input_hash=snapshot.input_hash,
        input_refs=input_refs,
        synthesis_checkpoint_ref=dict(snapshot.manifest["synthesis_checkpoint_ref"]),
        payload_sha256=_hash(synthesis_result),
        synthesis_result=synthesis_result,
    )


def build_verification_checkpoint(
    projection: VerificationInputProjection,
    *,
    verification_result: Mapping[str, Any] | object,
) -> ScanCheckpoint:
    """Build a Verification checkpoint from a projection and a supplied result.

    The caller supplies the Verification result mapping. This function never
    calls a model and never writes a database row.
    """

    _validate_projection(projection)
    compact_result = _compact_verification_result(verification_result)
    if compact_result is None:
        gate_result = {
            "gate": VERIFICATION_CHECKPOINT_CONTRACT,
            "status": "validation_failed",
            "reasons": [{"code": "malformed_verification_result"}],
            "warnings": [],
        }
        output_refs: list[dict[str, str]] = []
        verification_payload: Mapping[str, Any] | None = None
    else:
        gate_result = {
            "gate": VERIFICATION_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        }
        output_payload_hash = _hash(compact_result)
        output_refs = [
            {
                "id": "VRO-"
                + _hash(
                    {
                        "run_id": projection.run_id,
                        "stage": VERIFICATION_STAGE,
                        "attempt": projection.attempt,
                        "sha256": output_payload_hash,
                    }
                )[:32].upper(),
                "sha256": output_payload_hash,
                "kind": "verification_result",
            }
        ]
        verification_payload = compact_result

    snapshot: dict[str, Any] = {
        "contract": VERIFICATION_CHECKPOINT_CONTRACT,
        "run_id": projection.run_id,
        "stage": VERIFICATION_STAGE,
        "attempt": projection.attempt,
        "input_hash": projection.input_hash,
        "input_refs": [dict(reference) for reference in projection.input_refs],
        "output_refs": output_refs,
        "gate_results": [gate_result],
        "input_snapshot_ref": projection.snapshot_ref,
        "synthesis_checkpoint_ref": dict(projection.synthesis_checkpoint_ref),
        "payload_sha256": projection.payload_sha256,
    }
    if verification_payload is not None:
        snapshot["verification_result"] = verification_payload
    output_hash = _hash(snapshot)
    checkpoint_id = "CP-" + _hash(
        {
            "run_id": projection.run_id,
            "stage": VERIFICATION_STAGE,
            "attempt": projection.attempt,
            "contract": VERIFICATION_CHECKPOINT_CONTRACT,
            "output_hash": output_hash,
        }
    )[:32].upper()
    return ScanCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=projection.run_id,
        stage_name=VERIFICATION_STAGE,
        attempt=projection.attempt,
        contract_version=VERIFICATION_CHECKPOINT_CONTRACT,
        input_hash=projection.input_hash,
        output_hash=output_hash,
        gate_result=gate_result,
        snapshot=snapshot,
    )


def empty_verification_result() -> dict[str, Any]:
    """Valid empty Verification payload used by the prepare-only default wrap."""

    return {"verification_record": ""}


def _validate_projection(projection: VerificationInputProjection) -> None:
    if projection.contract != VERIFICATION_PROJECTION_CONTRACT:
        raise VerificationInputSnapshotIntegrityError("Verification projection contract is invalid.")
    if (
        not projection.run_id
        or projection.stage != VERIFICATION_STAGE
        or projection.attempt < 1
        or not projection.snapshot_id
        or not projection.input_hash
    ):
        raise VerificationInputSnapshotIntegrityError("Verification projection identity is invalid.")
    if _hash(dict(projection.synthesis_result)) != projection.payload_sha256:
        raise VerificationInputSnapshotIntegrityError(
            "Verification projection payload hash does not match Synthesis result."
        )
    if (
        not projection.input_refs
        or projection.input_refs[0].get("kind") != "synthesis_checkpoint"
        or projection.input_refs[0].get("id") != projection.synthesis_checkpoint_ref.get("checkpoint_id")
    ):
        raise VerificationInputSnapshotIntegrityError(
            "Verification projection does not bind the Synthesis checkpoint."
        )


def _synthesis_result_from_checkpoint(checkpoint: ScanCheckpoint) -> dict[str, Any]:
    if checkpoint.stage_name != SYNTHESIS_STAGE:
        raise ValueError("Verification input requires a passed Synthesis checkpoint.")
    if checkpoint.contract_version != SYNTHESIS_CHECKPOINT_CONTRACT:
        raise ValueError("Verification input requires a Synthesis checkpoint.")
    synthesis_result = checkpoint.snapshot.get("synthesis_result")
    if not isinstance(synthesis_result, Mapping):
        raise ValueError("Verification input requires a compact Synthesis result.")
    compact = _json_safe_mapping(synthesis_result)
    if compact is None:
        raise ValueError("Verification input Synthesis result must be JSON-safe.")
    return compact


def _compact_verification_result(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != _VERIFICATION_RESULT_KEYS:
        return None
    record = value.get("verification_record")
    if not isinstance(record, str):
        return None
    return {"verification_record": record}


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
