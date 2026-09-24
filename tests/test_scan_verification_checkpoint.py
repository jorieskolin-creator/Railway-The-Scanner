"""Hermetic contracts for the Verification input projection and checkpoint builders."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_foresight_checkpoint import FORESIGHT_CHECKPOINT_CONTRACT  # noqa: E402
from scan_supervisor import ScanCheckpoint, VerificationInputSnapshotIntegrityError  # noqa: E402
from scan_synthesis_checkpoint import (  # noqa: E402
    SYNTHESIS_CHECKPOINT_CONTRACT,
    empty_synthesis_result,
)
from scan_verification_checkpoint import (  # noqa: E402
    VERIFICATION_CHECKPOINT_CONTRACT,
    VERIFICATION_PROJECTION_CONTRACT,
    build_verification_checkpoint,
    build_verification_input_projection,
    empty_verification_result,
)
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402


def _synthesis_result():
    return {
        "strategic_synthesis": "A distinctive synthesis sentence about customer autonomy.",
    }


def _synthesis_checkpoint(*, run_id="RUN-VER-001", status="passed", output_hash="b" * 64, result=None):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "S" * 32,
        run_id=run_id,
        stage_name="strategic_synthesis",
        attempt=1,
        contract_version=SYNTHESIS_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash=output_hash,
        gate_result={
            "gate": SYNTHESIS_CHECKPOINT_CONTRACT,
            "status": status,
            "reasons": [],
            "warnings": [],
        },
        snapshot={
            "contract": SYNTHESIS_CHECKPOINT_CONTRACT,
            "synthesis_result": _synthesis_result() if result is None else result,
        },
    )


def _foresight_checkpoint(*, run_id="RUN-VER-001"):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "F" * 32,
        run_id=run_id,
        stage_name="foresight",
        attempt=1,
        contract_version=FORESIGHT_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result={
            "gate": FORESIGHT_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        },
        snapshot={
            "contract": FORESIGHT_CHECKPOINT_CONTRACT,
            "foresight_result": {
                "dimension_forces": [{"force": "A tightening climate policy window."}],
                "cross_impact_chain": "Policy pressure raises operating cost.",
                "scenarios": {"base": "Delayed adaptation reduces optionality."},
                "climate_exposure": {"level": "elevated"},
            },
        },
    )


def _snapshot(checkpoint=None):
    checkpoint = _synthesis_checkpoint() if checkpoint is None else checkpoint
    return build_verification_input_snapshot(
        run_id="RUN-VER-001",
        attempt=1,
        synthesis_checkpoint=checkpoint,
    )


def test_projection_is_deterministic_and_binds_only_the_synthesis_checkpoint():
    checkpoint = _synthesis_checkpoint()
    snapshot = _snapshot(checkpoint)

    first = build_verification_input_projection(snapshot, synthesis_checkpoint=checkpoint)
    second = build_verification_input_projection(snapshot, synthesis_checkpoint=checkpoint)

    assert first == second
    assert first.contract == VERIFICATION_PROJECTION_CONTRACT
    assert first.stage == "verification"
    assert first.input_hash == snapshot.input_hash
    assert first.snapshot_id == snapshot.snapshot_id
    assert [reference["id"] for reference in first.input_refs] == [checkpoint.checkpoint_id]
    assert first.input_refs[0]["kind"] == "synthesis_checkpoint"
    assert (
        first.synthesis_result["strategic_synthesis"]
        == "A distinctive synthesis sentence about customer autonomy."
    )


def test_projection_rejects_tampered_or_non_synthesis_checkpoints():
    checkpoint = _synthesis_checkpoint()
    snapshot = _snapshot(checkpoint)

    with pytest.raises(VerificationInputSnapshotIntegrityError, match="does not match"):
        build_verification_input_projection(
            snapshot, synthesis_checkpoint=_synthesis_checkpoint(output_hash="d" * 64)
        )
    with pytest.raises(VerificationInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_verification_input_projection(
            snapshot, synthesis_checkpoint=_synthesis_checkpoint(status="blocked")
        )
    with pytest.raises(VerificationInputSnapshotIntegrityError, match="cannot be reconstructed"):
        build_verification_input_projection(snapshot, synthesis_checkpoint=_foresight_checkpoint())


def test_checkpoint_hashes_are_stable_and_omit_synthesis_text_and_raw_bytes():
    checkpoint = _synthesis_checkpoint()
    projection = build_verification_input_projection(
        _snapshot(checkpoint), synthesis_checkpoint=checkpoint
    )
    result = empty_verification_result()

    first = build_verification_checkpoint(projection, verification_result=result)
    second = build_verification_checkpoint(projection, verification_result=dict(result))

    assert first == second
    assert first.contract_version == VERIFICATION_CHECKPOINT_CONTRACT
    assert first.stage_name == "verification"
    assert first.input_hash == projection.input_hash
    assert first.gate_result["status"] == "passed"
    assert first.snapshot["verification_result"] == result
    assert first.snapshot["payload_sha256"] == projection.payload_sha256
    assert "synthesis_result" not in first.snapshot
    snapshot_text = str(first.snapshot)
    assert "customer autonomy" not in snapshot_text
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


def test_malformed_verification_result_cannot_produce_a_passed_gate():
    checkpoint = _synthesis_checkpoint()
    projection = build_verification_input_projection(
        _snapshot(checkpoint), synthesis_checkpoint=checkpoint
    )

    missing = build_verification_checkpoint(projection, verification_result={})
    extra = build_verification_checkpoint(
        projection,
        verification_result={**empty_verification_result(), "commercial_pitch": "Buy now."},
    )
    bad_type = build_verification_checkpoint(
        projection,
        verification_result={"verification": ["not-a-string"]},
    )

    assert missing.gate_result["status"] == "validation_failed"
    assert extra.gate_result["status"] == "validation_failed"
    assert bad_type.gate_result["status"] == "validation_failed"
    assert missing.snapshot["output_refs"] == []
    assert "verification_result" not in missing.snapshot
    assert extra.gate_result["reasons"] == [{"code": "malformed_verification_result"}]


def test_empty_verification_result_can_still_pass():
    checkpoint = _synthesis_checkpoint(result=empty_synthesis_result())
    projection = build_verification_input_projection(
        _snapshot(checkpoint), synthesis_checkpoint=checkpoint
    )

    result = build_verification_checkpoint(projection, verification_result=empty_verification_result())

    assert result.gate_result["status"] == "passed"
    assert result.snapshot["verification_result"]["verification"] == ""


def test_module_does_not_call_verification_generation_or_acquire_content():
    source = Path(_BACKEND, "scan_verification_checkpoint.py").read_text(encoding="utf-8")

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
    assert "company_summary" not in source
    assert "final_index" not in source
    assert "commit_verification" not in source
