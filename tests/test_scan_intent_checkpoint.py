"""Hermetic contracts for the Intent input projection and checkpoint builders."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_intent_checkpoint import (  # noqa: E402
    INTENT_CHECKPOINT_CONTRACT,
    INTENT_PROJECTION_CONTRACT,
    build_intent_checkpoint,
    build_intent_input_projection,
)
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import IntentInputSnapshotIntegrityError  # noqa: E402


def _materials():
    return prepare_scan_materials(
        run_id="RUN-INTENT-001",
        report_text="Submitted company context.",
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
    ).materials


def _retrievals():
    return (
        prepare_source_retrieval(
            run_id="RUN-INTENT-001",
            stage_name="intent",
            attempt=1,
            retrieval_origin="intent_web_acquisition",
            source_url="https://example.test/strategy/",
            retrieved_at=datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc),
            http_status=200,
            content_type="text/html",
            response_content=b"<html><body>Acquired page</body></html>",
            extracted_text="Acquired page",
            parser_route="html/main-text/v1",
            readability_status="readable",
            retrieval_outcome="succeeded",
        ),
    )


def _snapshot(materials=None, retrievals=None):
    materials = _materials() if materials is None else materials
    retrievals = _retrievals() if retrievals is None else retrievals
    return build_intent_input_snapshot(
        run_id="RUN-INTENT-001",
        attempt=1,
        materials=materials,
        retrievals=retrievals,
    )


def _valid_intent_result():
    return {
        "score": 12,
        "breakdown": [
            {
                "name": "Customer",
                "score": 12,
                "goodCount": 1,
                "badCount": 0,
                "matches": ["customer"],
                "matches_detail": {"good": ["customer"], "bad": []},
            }
        ],
        "metrics": {
            "friction_index": 0.0,
            "vibrancy_score": 10,
            "total_good_mentions": 1,
            "total_bad_mentions": 0,
        },
    }


def test_projection_is_deterministic_and_uses_only_snapshot_bound_inputs():
    materials = _materials()
    retrievals = _retrievals()
    snapshot = _snapshot(materials, retrievals)

    first = build_intent_input_projection(
        snapshot, materials=tuple(reversed(materials)), retrievals=tuple(reversed(retrievals))
    )
    second = build_intent_input_projection(snapshot, materials=materials, retrievals=retrievals)

    assert first == second
    assert first.contract == INTENT_PROJECTION_CONTRACT
    assert first.stage == "intent"
    assert first.input_hash == snapshot.input_hash
    assert first.snapshot_id == snapshot.snapshot_id
    ordered_material_ids = [material.material_id for material in sorted(materials, key=lambda item: item.material_id)]
    ordered_retrieval_ids = [retrieval.retrieval_id for retrieval in retrievals]
    assert [reference["id"] for reference in first.input_refs] == ordered_material_ids + ordered_retrieval_ids
    assert first.concatenated_text == "\n\n".join(
        [material.extracted_text for material in sorted(materials, key=lambda item: item.material_id)]
        + [retrieval.extracted_text for retrieval in retrievals]
    )
    assert all(reference["kind"] in {"material", "source_retrieval"} for reference in first.input_refs)


def test_projection_rejects_added_omitted_or_tampered_inputs():
    materials = _materials()
    retrievals = _retrievals()
    snapshot = _snapshot(materials, retrievals)
    extra_material = prepare_scan_materials(
        run_id="RUN-INTENT-001",
        report_text="",
        uploads=[{"filename": "extra.txt", "content": b"Untracked extra material"}],
    ).materials[0]
    tampered_retrieval = retrievals[0].__class__(
        **{**retrievals[0].__dict__, "extracted_text": "tampered acquired page"}
    )

    with pytest.raises(IntentInputSnapshotIntegrityError, match="does not match"):
        build_intent_input_projection(
            snapshot, materials=(*materials, extra_material), retrievals=retrievals
        )
    with pytest.raises(IntentInputSnapshotIntegrityError, match="does not match"):
        build_intent_input_projection(snapshot, materials=materials[1:], retrievals=retrievals)
    with pytest.raises(IntentInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_intent_input_projection(snapshot, materials=materials, retrievals=[tampered_retrieval])


def test_checkpoint_hashes_are_stable_and_omit_text_and_raw_bytes():
    projection = build_intent_input_projection(
        _snapshot(), materials=_materials(), retrievals=_retrievals()
    )
    result = _valid_intent_result()

    first = build_intent_checkpoint(projection, intent_result=result)
    second = build_intent_checkpoint(projection, intent_result=dict(result))

    assert first == second
    assert first.contract_version == INTENT_CHECKPOINT_CONTRACT
    assert first.stage_name == "intent"
    assert first.input_hash == projection.input_hash
    assert first.gate_result["status"] == "passed"
    assert first.snapshot["gate_results"][0]["status"] == "passed"
    assert first.snapshot["input_refs"] == [dict(reference) for reference in projection.input_refs]
    assert first.snapshot["intent_result"]["score"] == 12
    assert first.snapshot["payload_sha256"] == projection.payload_sha256
    snapshot_text = str(first.snapshot)
    assert "Submitted company context." not in snapshot_text
    assert "Customer-provided brief" not in snapshot_text
    assert "Acquired page" not in snapshot_text
    assert "concatenated_text" not in first.snapshot
    assert "extracted_text" not in snapshot_text
    assert "response_content" not in snapshot_text
    assert "claim_support" not in snapshot_text


def test_malformed_intent_result_cannot_produce_a_passed_gate():
    projection = build_intent_input_projection(
        _snapshot(), materials=_materials(), retrievals=_retrievals()
    )

    missing_score = build_intent_checkpoint(
        projection,
        intent_result={"breakdown": [], "metrics": _valid_intent_result()["metrics"]},
    )
    extra_field = build_intent_checkpoint(
        projection,
        intent_result={**_valid_intent_result(), "evidence_id": "EVD-1"},
    )
    non_mapping = build_intent_checkpoint(projection, intent_result="not-a-result")

    assert missing_score.gate_result["status"] == "validation_failed"
    assert extra_field.gate_result["status"] == "validation_failed"
    assert non_mapping.gate_result["status"] == "validation_failed"
    assert missing_score.snapshot["output_refs"] == []
    assert "intent_result" not in missing_score.snapshot
    assert extra_field.gate_result["reasons"] == [{"code": "malformed_intent_result"}]


def test_empty_concatenated_text_can_still_pass_with_a_valid_result_shape():
    materials = prepare_scan_materials(
        run_id="RUN-INTENT-001",
        report_text="",
        uploads=[],
    ).materials
    snapshot = _snapshot(materials, retrievals=())
    projection = build_intent_input_projection(snapshot, materials=materials, retrievals=())

    checkpoint = build_intent_checkpoint(projection, intent_result=_valid_intent_result())

    assert projection.concatenated_text == ""
    assert checkpoint.gate_result["status"] == "passed"


def test_module_does_not_call_intent_or_acquire_content():
    source = Path(_BACKEND, "scan_intent_checkpoint.py").read_text(encoding="utf-8")

    assert "analyze_intent" not in source
    assert "scrape_strategic_content" not in source
    assert "scan_ingress" not in source
    assert "complete_step" not in source
    assert "jury_analysis" not in source
