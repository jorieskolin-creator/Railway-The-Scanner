"""Contracts for immutable, evidence-neutral Verification input manifests."""

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
from scan_synthesis_checkpoint import SYNTHESIS_CHECKPOINT_CONTRACT  # noqa: E402
from scan_verification_snapshot import (  # noqa: E402
    VERIFICATION_INPUT_SNAPSHOT_CONTRACT,
    assert_verification_input_snapshot_matches,
    build_verification_input_snapshot,
)


def _synthesis_checkpoint(*, run_id="RUN-VER-001", status="passed", output_hash="b" * 64):
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
            "synthesis_result": {
                "strategic_synthesis": "A climate-policy window is closing for this account."
            },
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


def test_verification_input_manifest_is_deterministic_and_contains_references_not_payload():
    checkpoint = _synthesis_checkpoint()

    first = build_verification_input_snapshot(
        run_id="RUN-VER-001", attempt=1, synthesis_checkpoint=checkpoint
    )
    second = build_verification_input_snapshot(
        run_id="RUN-VER-001", attempt=1, synthesis_checkpoint=checkpoint
    )

    assert first == second
    assert first.contract_version == VERIFICATION_INPUT_SNAPSHOT_CONTRACT
    assert first.snapshot_ref == f"verification-input://scan/{first.snapshot_id}"
    assert first.snapshot_id.startswith("VIS-")
    assert len(first.input_hash) == 64
    assert first.manifest["synthesis_checkpoint_ref"]["output_hash"] == checkpoint.output_hash
    assert first.manifest["synthesis_checkpoint_ref"]["checkpoint_id"] == checkpoint.checkpoint_id
    manifest_text = str(first.manifest)
    assert "climate-policy" not in manifest_text
    assert "synthesis_result" not in manifest_text
    assert "foresight_result" not in manifest_text
    assert "diagnosis_result" not in manifest_text
    assert "jury_result" not in manifest_text
    assert "intent_result" not in manifest_text
    assert "extracted_text" not in manifest_text
    assert "claim_support" not in manifest_text
    assert "foresight_eligible" not in manifest_text
    assert "kb_versions" not in first.manifest
    assert "candidate_refs" not in first.manifest
    assert "retrieval_refs" not in first.manifest
    assert set(first.manifest) == {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "synthesis_checkpoint_ref",
        "input_hash",
    }


def test_verification_input_manifest_rejects_non_passed_and_non_synthesis_checkpoints():
    with pytest.raises(ValueError, match="passed Synthesis checkpoint"):
        build_verification_input_snapshot(
            run_id="RUN-VER-001",
            attempt=1,
            synthesis_checkpoint=_synthesis_checkpoint(status="validation_failed"),
        )

    with pytest.raises(ValueError, match="does not match the Verification run"):
        build_verification_input_snapshot(
            run_id="RUN-VER-001",
            attempt=1,
            synthesis_checkpoint=_synthesis_checkpoint(run_id="RUN-OTHER"),
        )

    with pytest.raises(ValueError, match="passed Synthesis checkpoint"):
        build_verification_input_snapshot(
            run_id="RUN-VER-001",
            attempt=1,
            synthesis_checkpoint=_foresight_checkpoint(),
        )


def test_tampered_or_different_synthesis_checkpoint_cannot_match_the_manifest():
    checkpoint = _synthesis_checkpoint()
    snapshot = build_verification_input_snapshot(
        run_id="RUN-VER-001", attempt=1, synthesis_checkpoint=checkpoint
    )
    other = _synthesis_checkpoint(output_hash="d" * 64)

    with pytest.raises(
        VerificationInputSnapshotIntegrityError, match="does not match the verified durable inputs"
    ):
        assert_verification_input_snapshot_matches(snapshot, synthesis_checkpoint=other)

    with pytest.raises(VerificationInputSnapshotIntegrityError, match="cannot be reconstructed"):
        assert_verification_input_snapshot_matches(
            snapshot, synthesis_checkpoint=_synthesis_checkpoint(status="blocked")
        )


def test_module_does_not_call_verification_generation_or_acquire_content():
    source = Path(_BACKEND, "scan_verification_snapshot.py").read_text(encoding="utf-8")

    assert "analyze_intent" not in source
    assert "analyze_jury" not in source
    assert "generate_foresight" not in source
    assert "strategic_foresight" not in source
    assert "foresight_harvester" not in source
    assert "scrape_strategic_content" not in source
    assert "x_ray_search" not in source
    assert "complete_step" not in source
    assert "POST /scan" not in source
    assert "kb_versions" not in source
    assert "commit_verification" not in source
    assert "VerificationStageExecutor" not in source
