"""Hermetic contracts for the Foresight input projection and checkpoint builders."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_checkpoint import DIAGNOSIS_CHECKPOINT_CONTRACT  # noqa: E402
from scan_foresight_checkpoint import (  # noqa: E402
    FORESIGHT_CHECKPOINT_CONTRACT,
    FORESIGHT_PROJECTION_CONTRACT,
    build_foresight_checkpoint,
    build_foresight_input_projection,
    empty_foresight_result,
)
from scan_foresight_snapshot import build_foresight_input_snapshot  # noqa: E402
from scan_jury_checkpoint import JURY_CHECKPOINT_CONTRACT  # noqa: E402
from scan_supervisor import ForesightInputSnapshotIntegrityError, ScanCheckpoint  # noqa: E402


def _diagnosis_checkpoint(*, run_id="RUN-FOR-001", status="passed", output_hash="b" * 64):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "D" * 32,
        run_id=run_id,
        stage_name="diagnosis",
        attempt=1,
        contract_version=DIAGNOSIS_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash=output_hash,
        gate_result={
            "gate": DIAGNOSIS_CHECKPOINT_CONTRACT,
            "status": status,
            "reasons": [],
            "warnings": [],
        },
        snapshot={
            "contract": DIAGNOSIS_CHECKPOINT_CONTRACT,
            "diagnosis_result": {
                "diagnostic_summary": "The company is modernizing a cloud platform.",
                "evidence_based_risks": ["Legacy batch jobs remain in production."],
                "reality_score": 42,
                "archetype": "Modernizing",
            },
        },
    )


def _jury_checkpoint(*, run_id="RUN-FOR-001"):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "C" * 32,
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=1,
        contract_version=JURY_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result={
            "gate": JURY_CHECKPOINT_CONTRACT,
            "status": "passed",
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


def _snapshot(checkpoint=None):
    checkpoint = _diagnosis_checkpoint() if checkpoint is None else checkpoint
    return build_foresight_input_snapshot(
        run_id="RUN-FOR-001",
        attempt=1,
        diagnosis_checkpoint=checkpoint,
    )


def test_projection_is_deterministic_and_binds_only_the_diagnosis_checkpoint():
    checkpoint = _diagnosis_checkpoint()
    snapshot = _snapshot(checkpoint)

    first = build_foresight_input_projection(snapshot, diagnosis_checkpoint=checkpoint)
    second = build_foresight_input_projection(snapshot, diagnosis_checkpoint=checkpoint)

    assert first == second
    assert first.contract == FORESIGHT_PROJECTION_CONTRACT
    assert first.stage == "foresight"
    assert first.input_hash == snapshot.input_hash
    assert first.snapshot_id == snapshot.snapshot_id
    assert [reference["id"] for reference in first.input_refs] == [checkpoint.checkpoint_id]
    assert first.input_refs[0]["kind"] == "diagnosis_checkpoint"
    assert first.diagnosis_result["diagnostic_summary"] == (
        "The company is modernizing a cloud platform."
    )


def test_projection_rejects_tampered_or_non_diagnosis_checkpoints():
    checkpoint = _diagnosis_checkpoint()
    snapshot = _snapshot(checkpoint)

    with pytest.raises(ForesightInputSnapshotIntegrityError, match="does not match"):
        build_foresight_input_projection(
            snapshot, diagnosis_checkpoint=_diagnosis_checkpoint(output_hash="d" * 64)
        )
    with pytest.raises(ForesightInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_foresight_input_projection(
            snapshot, diagnosis_checkpoint=_diagnosis_checkpoint(status="blocked")
        )
    with pytest.raises(ForesightInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_foresight_input_projection(snapshot, diagnosis_checkpoint=_jury_checkpoint())


def test_checkpoint_hashes_are_stable_and_omit_diagnosis_text_and_raw_bytes():
    checkpoint = _diagnosis_checkpoint()
    projection = build_foresight_input_projection(
        _snapshot(checkpoint), diagnosis_checkpoint=checkpoint
    )
    result = empty_foresight_result()

    first = build_foresight_checkpoint(projection, foresight_result=result)
    second = build_foresight_checkpoint(projection, foresight_result=dict(result))

    assert first == second
    assert first.contract_version == FORESIGHT_CHECKPOINT_CONTRACT
    assert first.stage_name == "foresight"
    assert first.input_hash == projection.input_hash
    assert first.gate_result["status"] == "passed"
    assert first.snapshot["foresight_result"] == result
    assert first.snapshot["payload_sha256"] == projection.payload_sha256
    assert "diagnosis_result" not in first.snapshot
    snapshot_text = str(first.snapshot)
    assert "cloud platform" not in snapshot_text
    assert "diagnostic_summary" not in snapshot_text
    assert "concatenated_text" not in first.snapshot
    assert "extracted_text" not in snapshot_text
    assert "source_quote" not in snapshot_text
    assert "claim_support" not in snapshot_text
    assert "foresight_eligible" not in snapshot_text
    assert "kb_versions" not in snapshot_text
    assert "jury_result" not in snapshot_text
    assert "intent_result" not in snapshot_text


def test_malformed_foresight_result_cannot_produce_a_passed_gate():
    checkpoint = _diagnosis_checkpoint()
    projection = build_foresight_input_projection(
        _snapshot(checkpoint), diagnosis_checkpoint=checkpoint
    )

    missing = build_foresight_checkpoint(
        projection, foresight_result={"dimension_forces": [], "scenarios": {}}
    )
    extra = build_foresight_checkpoint(
        projection,
        foresight_result={**empty_foresight_result(), "kb_signals": [{"url": "https://kb.test"}]},
    )
    bad_forces = build_foresight_checkpoint(
        projection,
        foresight_result={**empty_foresight_result(), "dimension_forces": ["not-a-mapping"]},
    )

    assert missing.gate_result["status"] == "validation_failed"
    assert extra.gate_result["status"] == "validation_failed"
    assert bad_forces.gate_result["status"] == "validation_failed"
    assert missing.snapshot["output_refs"] == []
    assert "foresight_result" not in missing.snapshot
    assert extra.gate_result["reasons"] == [{"code": "malformed_foresight_result"}]


def test_empty_foresight_result_can_still_pass():
    checkpoint = _diagnosis_checkpoint()
    projection = build_foresight_input_projection(
        _snapshot(checkpoint), diagnosis_checkpoint=checkpoint
    )

    result = build_foresight_checkpoint(projection, foresight_result=empty_foresight_result())

    assert result.gate_result["status"] == "passed"
    assert result.snapshot["foresight_result"]["dimension_forces"] == []
    assert result.snapshot["foresight_result"]["cross_impact_chain"] == ""


def test_module_does_not_call_foresight_generation_or_acquire_content():
    source = Path(_BACKEND, "scan_foresight_checkpoint.py").read_text(encoding="utf-8")

    assert "generate_foresight" not in source
    assert "strategic_foresight" not in source
    assert "foresight_harvester" not in source
    assert "analyze_intent" not in source
    assert "scrape_strategic_content" not in source
    assert "x_ray_search" not in source
    assert "complete_step" not in source
    assert "kb_versions" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "strategic_synthesis" not in source
