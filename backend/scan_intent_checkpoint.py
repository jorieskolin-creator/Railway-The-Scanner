"""Versioned Intent input projection and output checkpoint contract.

These builders bind a verified Intent input snapshot to a deterministic text
projection and a separately versioned checkpoint proposal. They do not invoke
Intent scoring, persist state, or grant evidence, eligibility, or publication
meaning.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from scan_intent_snapshot import INTENT_STAGE, assert_intent_input_snapshot_matches
from scan_supervisor import (
    IntentInputSnapshot,
    IntentInputSnapshotIntegrityError,
    ScanCheckpoint,
    ScanMaterial,
    SourceRetrieval,
)


INTENT_PROJECTION_CONTRACT = "scanner.intent-projection/v1"
INTENT_CHECKPOINT_CONTRACT = "scanner.intent/v1"
_INTENT_RESULT_KEYS = {"score", "breakdown", "metrics"}
_INTENT_METRIC_KEYS = {
    "friction_index",
    "vibrancy_score",
    "total_good_mentions",
    "total_bad_mentions",
}


@dataclass(frozen=True)
class IntentInputProjection:
    """Exact adapter-facing view of one verified Intent input snapshot.

    `concatenated_text` is the only payload a later adapter may pass to the
    existing Intent function. It is not stored on the checkpoint.
    """

    contract: str
    run_id: str
    stage: str
    attempt: int
    snapshot_id: str
    snapshot_ref: str
    input_hash: str
    input_refs: tuple[Mapping[str, str], ...]
    payload_sha256: str
    concatenated_text: str


def build_intent_input_projection(
    snapshot: IntentInputSnapshot,
    *,
    materials: Sequence[ScanMaterial],
    retrievals: Sequence[SourceRetrieval],
) -> IntentInputProjection:
    """Project verified durable inputs into a versioned Intent envelope payload."""

    try:
        assert_intent_input_snapshot_matches(
            snapshot,
            materials=materials,
            retrievals=retrievals,
        )
    except IntentInputSnapshotIntegrityError:
        raise
    except (TypeError, ValueError) as error:
        raise IntentInputSnapshotIntegrityError(
            "Intent input projection cannot be built from durable inputs."
        ) from error

    ordered_materials = tuple(sorted(materials, key=lambda item: item.material_id))
    ordered_retrievals = tuple(sorted(retrievals, key=lambda item: item.retrieval_id))
    input_refs = tuple(
        [_material_input_ref(material) for material in ordered_materials]
        + [_retrieval_input_ref(retrieval) for retrieval in ordered_retrievals]
    )
    concatenated_text = "\n\n".join(
        [material.extracted_text for material in ordered_materials]
        + [retrieval.extracted_text for retrieval in ordered_retrievals]
    )
    return IntentInputProjection(
        contract=INTENT_PROJECTION_CONTRACT,
        run_id=snapshot.run_id,
        stage=INTENT_STAGE,
        attempt=snapshot.attempt,
        snapshot_id=snapshot.snapshot_id,
        snapshot_ref=snapshot.snapshot_ref,
        input_hash=snapshot.input_hash,
        input_refs=input_refs,
        payload_sha256=_sha256_text(concatenated_text),
        concatenated_text=concatenated_text,
    )


def build_intent_checkpoint(
    projection: IntentInputProjection,
    *,
    intent_result: Mapping[str, Any] | object,
) -> ScanCheckpoint:
    """Build an Intent output checkpoint from a projection and a supplied result.

    The caller supplies the Intent result mapping. This function never scores
    text and never writes a database row.
    """

    _validate_projection(projection)
    compact_result = _compact_intent_result(intent_result)
    if compact_result is None:
        gate_result = {
            "gate": INTENT_CHECKPOINT_CONTRACT,
            "status": "validation_failed",
            "reasons": [{"code": "malformed_intent_result"}],
            "warnings": [],
        }
        output_refs: list[dict[str, str]] = []
        intent_payload: Mapping[str, Any] | None = None
    else:
        gate_result = {
            "gate": INTENT_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        }
        output_payload_hash = _hash(compact_result)
        output_refs = [
            {
                "id": "IRO-"
                + _hash(
                    {
                        "run_id": projection.run_id,
                        "stage": INTENT_STAGE,
                        "attempt": projection.attempt,
                        "sha256": output_payload_hash,
                    }
                )[:32].upper(),
                "sha256": output_payload_hash,
                "kind": "intent_result",
            }
        ]
        intent_payload = compact_result

    snapshot: dict[str, Any] = {
        "contract": INTENT_CHECKPOINT_CONTRACT,
        "run_id": projection.run_id,
        "stage": INTENT_STAGE,
        "attempt": projection.attempt,
        "input_hash": projection.input_hash,
        "input_refs": [dict(reference) for reference in projection.input_refs],
        "output_refs": output_refs,
        "gate_results": [gate_result],
        "input_snapshot_ref": projection.snapshot_ref,
        "payload_sha256": projection.payload_sha256,
    }
    if intent_payload is not None:
        snapshot["intent_result"] = intent_payload
    output_hash = _hash(snapshot)
    checkpoint_id = "CP-" + _hash(
        {
            "run_id": projection.run_id,
            "stage": INTENT_STAGE,
            "attempt": projection.attempt,
            "contract": INTENT_CHECKPOINT_CONTRACT,
            "output_hash": output_hash,
        }
    )[:32].upper()
    return ScanCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=projection.run_id,
        stage_name=INTENT_STAGE,
        attempt=projection.attempt,
        contract_version=INTENT_CHECKPOINT_CONTRACT,
        input_hash=projection.input_hash,
        output_hash=output_hash,
        gate_result=gate_result,
        snapshot=snapshot,
    )


def _validate_projection(projection: IntentInputProjection) -> None:
    if projection.contract != INTENT_PROJECTION_CONTRACT:
        raise IntentInputSnapshotIntegrityError("Intent projection contract is invalid.")
    if (
        not projection.run_id
        or projection.stage != INTENT_STAGE
        or projection.attempt < 1
        or not projection.snapshot_id
        or not projection.input_hash
    ):
        raise IntentInputSnapshotIntegrityError("Intent projection identity is invalid.")
    if _sha256_text(projection.concatenated_text) != projection.payload_sha256:
        raise IntentInputSnapshotIntegrityError(
            "Intent projection payload hash does not match concatenated text."
        )


def _material_input_ref(material: ScanMaterial) -> dict[str, str]:
    return {
        "id": material.material_id,
        "sha256": material.extracted_text_sha256,
        "kind": "material",
    }


def _retrieval_input_ref(retrieval: SourceRetrieval) -> dict[str, str]:
    return {
        "id": retrieval.retrieval_id,
        "sha256": retrieval.extracted_text_sha256,
        "kind": "source_retrieval",
    }


def _compact_intent_result(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != _INTENT_RESULT_KEYS:
        return None
    if type(value.get("score")) is not int:
        return None
    breakdown = value.get("breakdown")
    metrics = value.get("metrics")
    if not isinstance(breakdown, list) or not isinstance(metrics, Mapping):
        return None
    if any(not isinstance(item, Mapping) for item in breakdown):
        return None
    if set(metrics) != _INTENT_METRIC_KEYS:
        return None
    compact = {
        "score": value["score"],
        "breakdown": [_json_safe_mapping(item) for item in breakdown],
        "metrics": _json_safe_mapping(metrics),
    }
    if any(item is None for item in compact["breakdown"]) or compact["metrics"] is None:
        return None
    return compact


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        return json.loads(
            json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )
    except (TypeError, ValueError):
        return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
