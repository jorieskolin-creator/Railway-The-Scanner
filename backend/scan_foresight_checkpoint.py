"""Versioned Foresight input projection and output checkpoint contract.

These builders bind a verified Foresight input snapshot to the compact
Diagnosis result a later adapter may read, and to a separately versioned
Foresight checkpoint proposal. They do not invoke Foresight generation,
persist state, copy Foresight KB content, or grant evidence, eligibility,
or publication meaning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from scan_diagnosis_checkpoint import DIAGNOSIS_CHECKPOINT_CONTRACT
from scan_diagnosis_snapshot import DIAGNOSIS_STAGE
from scan_foresight_snapshot import FORESIGHT_STAGE, assert_foresight_input_snapshot_matches
from scan_supervisor import (
    ForesightInputSnapshot,
    ForesightInputSnapshotIntegrityError,
    ScanCheckpoint,
)


FORESIGHT_PROJECTION_CONTRACT = "scanner.foresight-projection/v1"
FORESIGHT_CHECKPOINT_CONTRACT = "scanner.foresight/v1"
_FORESIGHT_RESULT_KEYS = {
    "dimension_forces",
    "cross_impact_chain",
    "scenarios",
    "climate_exposure",
}


@dataclass(frozen=True)
class ForesightInputProjection:
    """Exact adapter-facing view of one verified Foresight input snapshot.

    `diagnosis_result` is the only payload a later adapter may use as Foresight
    input. It is not stored on the Foresight checkpoint.
    """

    contract: str
    run_id: str
    stage: str
    attempt: int
    snapshot_id: str
    snapshot_ref: str
    input_hash: str
    input_refs: tuple[Mapping[str, str], ...]
    diagnosis_checkpoint_ref: Mapping[str, object]
    payload_sha256: str
    diagnosis_result: Mapping[str, Any]


def build_foresight_input_projection(
    snapshot: ForesightInputSnapshot,
    *,
    diagnosis_checkpoint: ScanCheckpoint,
) -> ForesightInputProjection:
    """Project a verified Diagnosis checkpoint into a versioned Foresight envelope payload."""

    try:
        assert_foresight_input_snapshot_matches(
            snapshot,
            diagnosis_checkpoint=diagnosis_checkpoint,
        )
        diagnosis_result = _diagnosis_result_from_checkpoint(diagnosis_checkpoint)
    except ForesightInputSnapshotIntegrityError:
        raise
    except (TypeError, ValueError) as error:
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input projection cannot be built from durable inputs."
        ) from error

    input_refs = (
        {
            "id": diagnosis_checkpoint.checkpoint_id,
            "sha256": diagnosis_checkpoint.output_hash,
            "kind": "diagnosis_checkpoint",
        },
    )
    return ForesightInputProjection(
        contract=FORESIGHT_PROJECTION_CONTRACT,
        run_id=snapshot.run_id,
        stage=FORESIGHT_STAGE,
        attempt=snapshot.attempt,
        snapshot_id=snapshot.snapshot_id,
        snapshot_ref=snapshot.snapshot_ref,
        input_hash=snapshot.input_hash,
        input_refs=input_refs,
        diagnosis_checkpoint_ref=dict(snapshot.manifest["diagnosis_checkpoint_ref"]),
        payload_sha256=_hash(diagnosis_result),
        diagnosis_result=diagnosis_result,
    )


def build_foresight_checkpoint(
    projection: ForesightInputProjection,
    *,
    foresight_result: Mapping[str, Any] | object,
) -> ScanCheckpoint:
    """Build a Foresight output checkpoint from a projection and a supplied result.

    The caller supplies the Foresight result mapping. This function never
    calls a model and never writes a database row.
    """

    _validate_projection(projection)
    compact_result = _compact_foresight_result(foresight_result)
    if compact_result is None:
        gate_result = {
            "gate": FORESIGHT_CHECKPOINT_CONTRACT,
            "status": "validation_failed",
            "reasons": [{"code": "malformed_foresight_result"}],
            "warnings": [],
        }
        output_refs: list[dict[str, str]] = []
        foresight_payload: Mapping[str, Any] | None = None
    else:
        gate_result = {
            "gate": FORESIGHT_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        }
        output_payload_hash = _hash(compact_result)
        output_refs = [
            {
                "id": "FRO-"
                + _hash(
                    {
                        "run_id": projection.run_id,
                        "stage": FORESIGHT_STAGE,
                        "attempt": projection.attempt,
                        "sha256": output_payload_hash,
                    }
                )[:32].upper(),
                "sha256": output_payload_hash,
                "kind": "foresight_result",
            }
        ]
        foresight_payload = compact_result

    snapshot: dict[str, Any] = {
        "contract": FORESIGHT_CHECKPOINT_CONTRACT,
        "run_id": projection.run_id,
        "stage": FORESIGHT_STAGE,
        "attempt": projection.attempt,
        "input_hash": projection.input_hash,
        "input_refs": [dict(reference) for reference in projection.input_refs],
        "output_refs": output_refs,
        "gate_results": [gate_result],
        "input_snapshot_ref": projection.snapshot_ref,
        "diagnosis_checkpoint_ref": dict(projection.diagnosis_checkpoint_ref),
        "payload_sha256": projection.payload_sha256,
    }
    if foresight_payload is not None:
        snapshot["foresight_result"] = foresight_payload
    output_hash = _hash(snapshot)
    checkpoint_id = "CP-" + _hash(
        {
            "run_id": projection.run_id,
            "stage": FORESIGHT_STAGE,
            "attempt": projection.attempt,
            "contract": FORESIGHT_CHECKPOINT_CONTRACT,
            "output_hash": output_hash,
        }
    )[:32].upper()
    return ScanCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=projection.run_id,
        stage_name=FORESIGHT_STAGE,
        attempt=projection.attempt,
        contract_version=FORESIGHT_CHECKPOINT_CONTRACT,
        input_hash=projection.input_hash,
        output_hash=output_hash,
        gate_result=gate_result,
        snapshot=snapshot,
    )


def empty_foresight_result() -> dict[str, Any]:
    """Valid empty Foresight payload used by the prepare-only default wrap."""

    return {
        "dimension_forces": [],
        "cross_impact_chain": "",
        "scenarios": {},
        "climate_exposure": {},
    }


def _validate_projection(projection: ForesightInputProjection) -> None:
    if projection.contract != FORESIGHT_PROJECTION_CONTRACT:
        raise ForesightInputSnapshotIntegrityError("Foresight projection contract is invalid.")
    if (
        not projection.run_id
        or projection.stage != FORESIGHT_STAGE
        or projection.attempt < 1
        or not projection.snapshot_id
        or not projection.input_hash
    ):
        raise ForesightInputSnapshotIntegrityError("Foresight projection identity is invalid.")
    if _hash(dict(projection.diagnosis_result)) != projection.payload_sha256:
        raise ForesightInputSnapshotIntegrityError(
            "Foresight projection payload hash does not match Diagnosis result."
        )
    if (
        not projection.input_refs
        or projection.input_refs[0].get("kind") != "diagnosis_checkpoint"
        or projection.input_refs[0].get("id") != projection.diagnosis_checkpoint_ref.get("checkpoint_id")
    ):
        raise ForesightInputSnapshotIntegrityError(
            "Foresight projection does not bind the Diagnosis checkpoint."
        )


def _diagnosis_result_from_checkpoint(checkpoint: ScanCheckpoint) -> dict[str, Any]:
    if checkpoint.stage_name != DIAGNOSIS_STAGE:
        raise ValueError("Foresight input requires a passed Diagnosis checkpoint.")
    if checkpoint.contract_version != DIAGNOSIS_CHECKPOINT_CONTRACT:
        raise ValueError("Foresight input requires a Diagnosis checkpoint.")
    diagnosis_result = checkpoint.snapshot.get("diagnosis_result")
    if not isinstance(diagnosis_result, Mapping):
        raise ValueError("Foresight input requires a compact Diagnosis result.")
    compact = _json_safe_mapping(diagnosis_result)
    if compact is None:
        raise ValueError("Foresight input Diagnosis result must be JSON-safe.")
    return compact


def _compact_foresight_result(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != _FORESIGHT_RESULT_KEYS:
        return None
    forces = value.get("dimension_forces")
    chain = value.get("cross_impact_chain")
    scenarios = value.get("scenarios")
    climate = value.get("climate_exposure")
    if not isinstance(forces, list) or any(not isinstance(item, Mapping) for item in forces):
        return None
    if not isinstance(chain, str):
        return None
    if not isinstance(scenarios, Mapping) or not isinstance(climate, Mapping):
        return None
    compact_forces = [_json_safe_mapping(item) for item in forces]
    compact_scenarios = _json_safe_mapping(scenarios)
    compact_climate = _json_safe_mapping(climate)
    if None in compact_forces or compact_scenarios is None or compact_climate is None:
        return None
    return {
        "dimension_forces": compact_forces,
        "cross_impact_chain": chain,
        "scenarios": compact_scenarios,
        "climate_exposure": compact_climate,
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
