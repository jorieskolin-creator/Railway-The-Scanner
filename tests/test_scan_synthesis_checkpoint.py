"""Hermetic contracts for the Synthesis input projection and checkpoint builders."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_checkpoint import DIAGNOSIS_CHECKPOINT_CONTRACT  # noqa: E402
from scan_foresight_checkpoint import FORESIGHT_CHECKPOINT_CONTRACT, empty_foresight_result  # noqa: E402
from scan_supervisor import ScanCheckpoint, SynthesisInputSnapshotIntegrityError  # noqa: E402
from scan_synthesis_checkpoint import (  # noqa: E402
    SYNTHESIS_CHECKPOINT_CONTRACT,
    SYNTHESIS_PROJECTION_CONTRACT,
    build_synthesis_checkpoint,
    build_synthesis_input_projection,
    empty_synthesis_result,
)
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402


def _foresight_result():
    return {
        "dimension_forces": [{"force": "A tightening climate policy window."}],
        "cross_impact_chain": "Policy pressure raises operating cost.",
        "scenarios": {"base": "Delayed adaptation reduces optionality."},
        "climate_exposure": {"level": "elevated"},
    }


def _foresight_checkpoint(*, run_id="RUN-SYN-001", status="passed", output_hash="b" * 64, result=None):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "F" * 32,
        run_id=run_id,
        stage_name="foresight",
        attempt=1,
        contract_version=FORESIGHT_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash=output_hash,
        gate_result={
            "gate": FORESIGHT_CHECKPOINT_CONTRACT,
            "status": status,
            "reasons": [],
            "warnings": [],
        },
        snapshot={
            "contract": FORESIGHT_CHECKPOINT_CONTRACT,
            "foresight_result": _foresight_result() if result is None else result,
        },
    )


def _diagnosis_checkpoint(*, run_id="RUN-SYN-001"):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "D" * 32,
        run_id=run_id,
        stage_name="diagnosis",
        attempt=1,
        contract_version=DIAGNOSIS_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result={
            "gate": DIAGNOSIS_CHECKPOINT_CONTRACT,
            "status": "passed",
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


def _snapshot(checkpoint=None):
    checkpoint = _foresight_checkpoint() if checkpoint is None else checkpoint
    return build_synthesis_input_snapshot(
        run_id="RUN-SYN-001",
        attempt=1,
        foresight_checkpoint=checkpoint,
    )


def test_projection_is_deterministic_and_binds_only_the_foresight_checkpoint():
    checkpoint = _foresight_checkpoint()
    snapshot = _snapshot(checkpoint)

    first = build_synthesis_input_projection(snapshot, foresight_checkpoint=checkpoint)
    second = build_synthesis_input_projection(snapshot, foresight_checkpoint=checkpoint)

    assert first == second
    assert first.contract == SYNTHESIS_PROJECTION_CONTRACT
    assert first.stage == "strategic_synthesis"
    assert first.input_hash == snapshot.input_hash
    assert first.snapshot_id == snapshot.snapshot_id
    assert [reference["id"] for reference in first.input_refs] == [checkpoint.checkpoint_id]
    assert first.input_refs[0]["kind"] == "foresight_checkpoint"
    assert first.foresight_result["cross_impact_chain"] == "Policy pressure raises operating cost."


def test_projection_rejects_tampered_or_non_foresight_checkpoints():
    checkpoint = _foresight_checkpoint()
    snapshot = _snapshot(checkpoint)

    with pytest.raises(SynthesisInputSnapshotIntegrityError, match="does not match"):
        build_synthesis_input_projection(
            snapshot, foresight_checkpoint=_foresight_checkpoint(output_hash="d" * 64)
        )
    with pytest.raises(SynthesisInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_synthesis_input_projection(
            snapshot, foresight_checkpoint=_foresight_checkpoint(status="blocked")
        )
    with pytest.raises(SynthesisInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_synthesis_input_projection(snapshot, foresight_checkpoint=_diagnosis_checkpoint())


def test_checkpoint_hashes_are_stable_and_omit_foresight_text_and_raw_bytes():
    checkpoint = _foresight_checkpoint()
    projection = build_synthesis_input_projection(
        _snapshot(checkpoint), foresight_checkpoint=checkpoint
    )
    result = empty_synthesis_result()

    first = build_synthesis_checkpoint(projection, synthesis_result=result)
    second = build_synthesis_checkpoint(projection, synthesis_result=dict(result))

    assert first == second
    assert first.contract_version == SYNTHESIS_CHECKPOINT_CONTRACT
    assert first.stage_name == "strategic_synthesis"
    assert first.input_hash == projection.input_hash
    assert first.gate_result["status"] == "passed"
    assert first.snapshot["synthesis_result"] == result
    assert first.snapshot["payload_sha256"] == projection.payload_sha256
    assert "foresight_result" not in first.snapshot
    snapshot_text = str(first.snapshot)
    assert "climate policy" not in snapshot_text
    assert "dimension_forces" not in snapshot_text
    assert "cross_impact_chain" not in snapshot_text
    assert "concatenated_text" not in first.snapshot
    assert "extracted_text" not in snapshot_text
    assert "diagnosis_result" not in snapshot_text
    assert "jury_result" not in snapshot_text
    assert "intent_result" not in snapshot_text
    assert "claim_support" not in snapshot_text
    assert "foresight_eligible" not in snapshot_text
    assert "kb_versions" not in snapshot_text


def test_malformed_synthesis_result_cannot_produce_a_passed_gate():
    checkpoint = _foresight_checkpoint()
    projection = build_synthesis_input_projection(
        _snapshot(checkpoint), foresight_checkpoint=checkpoint
    )

    missing = build_synthesis_checkpoint(projection, synthesis_result={})
    extra = build_synthesis_checkpoint(
        projection,
        synthesis_result={**empty_synthesis_result(), "commercial_pitch": "Buy now."},
    )
    bad_type = build_synthesis_checkpoint(
        projection,
        synthesis_result={"strategic_synthesis": ["not-a-string"]},
    )

    assert missing.gate_result["status"] == "validation_failed"
    assert extra.gate_result["status"] == "validation_failed"
    assert bad_type.gate_result["status"] == "validation_failed"
    assert missing.snapshot["output_refs"] == []
    assert "synthesis_result" not in missing.snapshot
    assert extra.gate_result["reasons"] == [{"code": "malformed_synthesis_result"}]


def test_empty_synthesis_result_can_still_pass():
    checkpoint = _foresight_checkpoint(result=empty_foresight_result())
    projection = build_synthesis_input_projection(
        _snapshot(checkpoint), foresight_checkpoint=checkpoint
    )

    result = build_synthesis_checkpoint(projection, synthesis_result=empty_synthesis_result())

    assert result.gate_result["status"] == "passed"
    assert result.snapshot["synthesis_result"]["strategic_synthesis"] == ""


def test_module_does_not_call_synthesis_generation_or_acquire_content():
    source = Path(_BACKEND, "scan_synthesis_checkpoint.py").read_text(encoding="utf-8")

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
    assert "verification" not in source
    assert "commit_synthesis" not in source
