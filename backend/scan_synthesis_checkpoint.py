"""Versioned Strategic Synthesis input projection and output checkpoint contract.

These builders bind a verified Synthesis input snapshot to the compact
Foresight result a later adapter may read, and to a separately versioned
Synthesis checkpoint proposal. They do not invoke Synthesis generation,
persist state, copy Foresight KB content, or grant evidence, eligibility,
or publication meaning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from scan_foresight_checkpoint import FORESIGHT_CHECKPOINT_CONTRACT
from scan_foresight_snapshot import FORESIGHT_STAGE
from scan_supervisor import (
    ScanCheckpoint,
    SynthesisInputSnapshot,
    SynthesisInputSnapshotIntegrityError,
)
from scan_synthesis_snapshot import SYNTHESIS_STAGE, assert_synthesis_input_snapshot_matches


SYNTHESIS_PROJECTION_CONTRACT = "scanner.synthesis-projection/v1"
SYNTHESIS_CHECKPOINT_CONTRACT = "scanner.synthesis/v1"
_SYNTHESIS_RESULT_KEYS = {
    "strategic_synthesis",
}


@dataclass(frozen=True)
class SynthesisInputProjection:
    """Exact adapter-facing view of one verified Synthesis input snapshot.

    `foresight_result` is the only payload a later adapter may use as Synthesis
    input. It is not stored on the Synthesis checkpoint.
    """

    contract: str
    run_id: str
    stage: str
    attempt: int
    snapshot_id: str
    snapshot_ref: str
    input_hash: str
    input_refs: tuple[Mapping[str, str], ...]
    foresight_checkpoint_ref: Mapping[str, object]
    payload_sha256: str
    foresight_result: Mapping[str, Any]


def build_synthesis_input_projection(
    snapshot: SynthesisInputSnapshot,
    *,
    foresight_checkpoint: ScanCheckpoint,
) -> SynthesisInputProjection:
    """Project a verified Foresight checkpoint into a versioned Synthesis envelope payload."""

    try:
        assert_synthesis_input_snapshot_matches(
            snapshot,
            foresight_checkpoint=foresight_checkpoint,
        )
        foresight_result = _foresight_result_from_checkpoint(foresight_checkpoint)
    except SynthesisInputSnapshotIntegrityError:
        raise
    except (TypeError, ValueError) as error:
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis input projection cannot be built from durable inputs."
        ) from error

    input_refs = (
        {
            "id": foresight_checkpoint.checkpoint_id,
            "sha256": foresight_checkpoint.output_hash,
            "kind": "foresight_checkpoint",
        },
    )
    return SynthesisInputProjection(
        contract=SYNTHESIS_PROJECTION_CONTRACT,
        run_id=snapshot.run_id,
        stage=SYNTHESIS_STAGE,
        attempt=snapshot.attempt,
        snapshot_id=snapshot.snapshot_id,
        snapshot_ref=snapshot.snapshot_ref,
        input_hash=snapshot.input_hash,
        input_refs=input_refs,
        foresight_checkpoint_ref=dict(snapshot.manifest["foresight_checkpoint_ref"]),
        payload_sha256=_hash(foresight_result),
        foresight_result=foresight_result,
    )


def build_synthesis_checkpoint(
    projection: SynthesisInputProjection,
    *,
    synthesis_result: Mapping[str, Any] | object,
) -> ScanCheckpoint:
    """Build a Synthesis output checkpoint from a projection and a supplied result.

    The caller supplies the Synthesis result mapping. This function never
    calls a model and never writes a database row.
    """

    _validate_projection(projection)
    compact_result = _compact_synthesis_result(synthesis_result)
    if compact_result is None:
        gate_result = {
            "gate": SYNTHESIS_CHECKPOINT_CONTRACT,
            "status": "validation_failed",
            "reasons": [{"code": "malformed_synthesis_result"}],
            "warnings": [],
        }
        output_refs: list[dict[str, str]] = []
        synthesis_payload: Mapping[str, Any] | None = None
    else:
        gate_result = {
            "gate": SYNTHESIS_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        }
        output_payload_hash = _hash(compact_result)
        output_refs = [
            {
                "id": "SYO-"
                + _hash(
                    {
                        "run_id": projection.run_id,
                        "stage": SYNTHESIS_STAGE,
                        "attempt": projection.attempt,
                        "sha256": output_payload_hash,
                    }
                )[:32].upper(),
                "sha256": output_payload_hash,
                "kind": "synthesis_result",
            }
        ]
        synthesis_payload = compact_result

    snapshot: dict[str, Any] = {
        "contract": SYNTHESIS_CHECKPOINT_CONTRACT,
        "run_id": projection.run_id,
        "stage": SYNTHESIS_STAGE,
        "attempt": projection.attempt,
        "input_hash": projection.input_hash,
        "input_refs": [dict(reference) for reference in projection.input_refs],
        "output_refs": output_refs,
        "gate_results": [gate_result],
        "input_snapshot_ref": projection.snapshot_ref,
        "foresight_checkpoint_ref": dict(projection.foresight_checkpoint_ref),
        "payload_sha256": projection.payload_sha256,
    }
    if synthesis_payload is not None:
        snapshot["synthesis_result"] = synthesis_payload
    output_hash = _hash(snapshot)
    checkpoint_id = "CP-" + _hash(
        {
            "run_id": projection.run_id,
            "stage": SYNTHESIS_STAGE,
            "attempt": projection.attempt,
            "contract": SYNTHESIS_CHECKPOINT_CONTRACT,
            "output_hash": output_hash,
        }
    )[:32].upper()
    return ScanCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=projection.run_id,
        stage_name=SYNTHESIS_STAGE,
        attempt=projection.attempt,
        contract_version=SYNTHESIS_CHECKPOINT_CONTRACT,
        input_hash=projection.input_hash,
        output_hash=output_hash,
        gate_result=gate_result,
        snapshot=snapshot,
    )


def empty_synthesis_result() -> dict[str, Any]:
    """Valid empty Synthesis payload used by the prepare-only default wrap."""

    return {"strategic_synthesis": ""}


def _validate_projection(projection: SynthesisInputProjection) -> None:
    if projection.contract != SYNTHESIS_PROJECTION_CONTRACT:
        raise SynthesisInputSnapshotIntegrityError("Synthesis projection contract is invalid.")
    if (
        not projection.run_id
        or projection.stage != SYNTHESIS_STAGE
        or projection.attempt < 1
        or not projection.snapshot_id
        or not projection.input_hash
    ):
        raise SynthesisInputSnapshotIntegrityError("Synthesis projection identity is invalid.")
    if _hash(dict(projection.foresight_result)) != projection.payload_sha256:
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis projection payload hash does not match Foresight result."
        )
    if (
        not projection.input_refs
        or projection.input_refs[0].get("kind") != "foresight_checkpoint"
        or projection.input_refs[0].get("id") != projection.foresight_checkpoint_ref.get("checkpoint_id")
    ):
        raise SynthesisInputSnapshotIntegrityError(
            "Synthesis projection does not bind the Foresight checkpoint."
        )


def _foresight_result_from_checkpoint(checkpoint: ScanCheckpoint) -> dict[str, Any]:
    if checkpoint.stage_name != FORESIGHT_STAGE:
        raise ValueError("Synthesis input requires a passed Foresight checkpoint.")
    if checkpoint.contract_version != FORESIGHT_CHECKPOINT_CONTRACT:
        raise ValueError("Synthesis input requires a Foresight checkpoint.")
    foresight_result = checkpoint.snapshot.get("foresight_result")
    if not isinstance(foresight_result, Mapping):
        raise ValueError("Synthesis input requires a compact Foresight result.")
    compact = _json_safe_mapping(foresight_result)
    if compact is None:
        raise ValueError("Synthesis input Foresight result must be JSON-safe.")
    return compact


def _compact_synthesis_result(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != _SYNTHESIS_RESULT_KEYS:
        return None
    synthesis = value.get("strategic_synthesis")
    if not isinstance(synthesis, str):
        return None
    return {"strategic_synthesis": synthesis}


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
