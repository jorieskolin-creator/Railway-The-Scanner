"""Contracts for immutable, evidence-neutral Strategic Synthesis input manifests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_checkpoint import DIAGNOSIS_CHECKPOINT_CONTRACT  # noqa: E402
from scan_foresight_checkpoint import FORESIGHT_CHECKPOINT_CONTRACT  # noqa: E402
from scan_supervisor import ScanCheckpoint, SynthesisInputSnapshotIntegrityError  # noqa: E402
from scan_synthesis_snapshot import (  # noqa: E402
    SYNTHESIS_INPUT_SNAPSHOT_CONTRACT,
    assert_synthesis_input_snapshot_matches,
    build_synthesis_input_snapshot,
)


def _foresight_checkpoint(*, run_id="RUN-SYN-001", status="passed", output_hash="b" * 64):
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
            "foresight_result": {
                "dimension_forces": ["A tightening climate policy window."],
                "cross_impact_chain": ["Policy pressure raises operating cost."],
                "scenarios": ["Delayed adaptation reduces optionality."],
                "climate_exposure": "elevated",
            },
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


def test_synthesis_input_manifest_is_deterministic_and_contains_references_not_payload():
    checkpoint = _foresight_checkpoint()

    first = build_synthesis_input_snapshot(
        run_id="RUN-SYN-001", attempt=1, foresight_checkpoint=checkpoint
    )
    second = build_synthesis_input_snapshot(
        run_id="RUN-SYN-001", attempt=1, foresight_checkpoint=checkpoint
    )

    assert first == second
    assert first.contract_version == SYNTHESIS_INPUT_SNAPSHOT_CONTRACT
    assert first.snapshot_ref == f"synthesis-input://scan/{first.snapshot_id}"
    assert first.snapshot_id.startswith("SIS-")
    assert len(first.input_hash) == 64
    assert first.manifest["foresight_checkpoint_ref"]["output_hash"] == checkpoint.output_hash
    assert first.manifest["foresight_checkpoint_ref"]["checkpoint_id"] == checkpoint.checkpoint_id
    manifest_text = str(first.manifest)
    assert "climate policy" not in manifest_text
    assert "dimension_forces" not in manifest_text
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
        "foresight_checkpoint_ref",
        "input_hash",
    }


def test_synthesis_input_manifest_rejects_non_passed_and_non_foresight_checkpoints():
    with pytest.raises(ValueError, match="passed Foresight checkpoint"):
        build_synthesis_input_snapshot(
            run_id="RUN-SYN-001",
            attempt=1,
            foresight_checkpoint=_foresight_checkpoint(status="validation_failed"),
        )

    with pytest.raises(ValueError, match="does not match the Synthesis run"):
        build_synthesis_input_snapshot(
            run_id="RUN-SYN-001",
            attempt=1,
            foresight_checkpoint=_foresight_checkpoint(run_id="RUN-OTHER"),
        )

    with pytest.raises(ValueError, match="passed Foresight checkpoint"):
        build_synthesis_input_snapshot(
            run_id="RUN-SYN-001",
            attempt=1,
            foresight_checkpoint=_diagnosis_checkpoint(),
        )


def test_tampered_or_different_foresight_checkpoint_cannot_match_the_manifest():
    checkpoint = _foresight_checkpoint()
    snapshot = build_synthesis_input_snapshot(
        run_id="RUN-SYN-001", attempt=1, foresight_checkpoint=checkpoint
    )
    other = _foresight_checkpoint(output_hash="d" * 64)

    with pytest.raises(
        SynthesisInputSnapshotIntegrityError, match="does not match the verified durable inputs"
    ):
        assert_synthesis_input_snapshot_matches(snapshot, foresight_checkpoint=other)

    with pytest.raises(SynthesisInputSnapshotIntegrityError, match="cannot be reconstructed"):
        assert_synthesis_input_snapshot_matches(
            snapshot, foresight_checkpoint=_foresight_checkpoint(status="blocked")
        )


def test_module_does_not_call_synthesis_generation_or_acquire_content():
    source = Path(_BACKEND, "scan_synthesis_snapshot.py").read_text(encoding="utf-8")

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
    assert "commit_synthesis" not in source
    assert "VerificationStage" not in source
