"""Hermetic contracts for the Diagnosis input projection and checkpoint builders."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_checkpoint import (  # noqa: E402
    DIAGNOSIS_CHECKPOINT_CONTRACT,
    DIAGNOSIS_PROJECTION_CONTRACT,
    build_diagnosis_checkpoint,
    build_diagnosis_input_projection,
    empty_diagnosis_result,
)
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_intent_checkpoint import INTENT_CHECKPOINT_CONTRACT  # noqa: E402
from scan_jury_checkpoint import JURY_CHECKPOINT_CONTRACT  # noqa: E402
from scan_supervisor import DiagnosisInputSnapshotIntegrityError, ScanCheckpoint  # noqa: E402


def _jury_checkpoint(*, run_id="RUN-DIAG-001", status="passed", output_hash="b" * 64):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "C" * 32,
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=1,
        contract_version=JURY_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash=output_hash,
        gate_result={
            "gate": JURY_CHECKPOINT_CONTRACT,
            "status": status,
            "reasons": [],
            "warnings": [],
        },
        snapshot={
            "contract": JURY_CHECKPOINT_CONTRACT,
            "jury_result": {
                "juries": [
                    {
                        "jury": "ARCHITECTURAL JURY (Engineering Reality)",
                        "findings": [
                            {
                                "supported_claim": "The company operates a cloud platform.",
                                "source_quote": "We run a cloud platform",
                            }
                        ],
                    }
                ]
            },
        },
    )


def _intent_checkpoint(*, run_id="RUN-DIAG-001"):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "A" * 32,
        run_id=run_id,
        stage_name="intent",
        attempt=1,
        contract_version=INTENT_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result={
            "gate": INTENT_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        },
        snapshot={"contract": INTENT_CHECKPOINT_CONTRACT, "intent_result": {"score": 12}},
    )


def _snapshot(checkpoint=None):
    checkpoint = _jury_checkpoint() if checkpoint is None else checkpoint
    return build_diagnosis_input_snapshot(
        run_id="RUN-DIAG-001",
        attempt=1,
        jury_checkpoint=checkpoint,
    )


def test_projection_is_deterministic_and_binds_only_the_jury_checkpoint():
    checkpoint = _jury_checkpoint()
    snapshot = _snapshot(checkpoint)

    first = build_diagnosis_input_projection(snapshot, jury_checkpoint=checkpoint)
    second = build_diagnosis_input_projection(snapshot, jury_checkpoint=checkpoint)

    assert first == second
    assert first.contract == DIAGNOSIS_PROJECTION_CONTRACT
    assert first.stage == "diagnosis"
    assert first.input_hash == snapshot.input_hash
    assert first.snapshot_id == snapshot.snapshot_id
    assert [reference["id"] for reference in first.input_refs] == [checkpoint.checkpoint_id]
    assert first.input_refs[0]["kind"] == "jury_checkpoint"
    assert first.jury_result["juries"][0]["findings"][0]["source_quote"] == "We run a cloud platform"


def test_projection_rejects_tampered_or_non_jury_checkpoints():
    checkpoint = _jury_checkpoint()
    snapshot = _snapshot(checkpoint)

    with pytest.raises(DiagnosisInputSnapshotIntegrityError, match="does not match"):
        build_diagnosis_input_projection(
            snapshot, jury_checkpoint=_jury_checkpoint(output_hash="d" * 64)
        )
    with pytest.raises(DiagnosisInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_diagnosis_input_projection(
            snapshot, jury_checkpoint=_jury_checkpoint(status="blocked")
        )
    with pytest.raises(DiagnosisInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_diagnosis_input_projection(snapshot, jury_checkpoint=_intent_checkpoint())


def test_checkpoint_hashes_are_stable_and_omit_jury_text_and_raw_bytes():
    checkpoint = _jury_checkpoint()
    projection = build_diagnosis_input_projection(_snapshot(checkpoint), jury_checkpoint=checkpoint)
    result = empty_diagnosis_result()

    first = build_diagnosis_checkpoint(projection, diagnosis_result=result)
    second = build_diagnosis_checkpoint(projection, diagnosis_result=dict(result))

    assert first == second
    assert first.contract_version == DIAGNOSIS_CHECKPOINT_CONTRACT
    assert first.stage_name == "diagnosis"
    assert first.input_hash == projection.input_hash
    assert first.gate_result["status"] == "passed"
    assert first.snapshot["diagnosis_result"] == result
    assert first.snapshot["payload_sha256"] == projection.payload_sha256
    assert "jury_result" not in first.snapshot
    snapshot_text = str(first.snapshot)
    assert "cloud platform" not in snapshot_text
    assert "We run a cloud platform" not in snapshot_text
    assert "concatenated_text" not in first.snapshot
    assert "extracted_text" not in snapshot_text
    assert "source_quote" not in snapshot_text
    assert "claim_support" not in snapshot_text
    assert "strategic_synthesis" not in snapshot_text


def test_malformed_diagnosis_result_cannot_produce_a_passed_gate():
    checkpoint = _jury_checkpoint()
    projection = build_diagnosis_input_projection(_snapshot(checkpoint), jury_checkpoint=checkpoint)

    missing = build_diagnosis_checkpoint(
        projection, diagnosis_result={"diagnostic_summary": "", "archetype": "At Risk"}
    )
    extra = build_diagnosis_checkpoint(
        projection,
        diagnosis_result={**empty_diagnosis_result(), "strategic_synthesis": "do not store"},
    )
    bad_archetype = build_diagnosis_checkpoint(
        projection,
        diagnosis_result={**empty_diagnosis_result(), "archetype": "Unknown"},
    )

    assert missing.gate_result["status"] == "validation_failed"
    assert extra.gate_result["status"] == "validation_failed"
    assert bad_archetype.gate_result["status"] == "validation_failed"
    assert missing.snapshot["output_refs"] == []
    assert "diagnosis_result" not in missing.snapshot
    assert extra.gate_result["reasons"] == [{"code": "malformed_diagnosis_result"}]


def test_empty_diagnosis_result_can_still_pass():
    checkpoint = _jury_checkpoint()
    projection = build_diagnosis_input_projection(_snapshot(checkpoint), jury_checkpoint=checkpoint)

    result = build_diagnosis_checkpoint(projection, diagnosis_result=empty_diagnosis_result())

    assert result.gate_result["status"] == "passed"
    assert result.snapshot["diagnosis_result"]["diagnostic_summary"] == ""
    assert result.snapshot["diagnosis_result"]["reality_score"] == 0


def test_module_does_not_call_diagnosis_or_acquire_content():
    source = Path(_BACKEND, "scan_diagnosis_checkpoint.py").read_text(encoding="utf-8")

    assert "analyze_intent" not in source
    assert "scrape_strategic_content" not in source
    assert "x_ray_search" not in source
    assert "complete_step" not in source
    assert "foresight" not in source.lower()
    assert "from main" not in source
    assert "import main" not in source
