"""Contracts for immutable, evidence-neutral Foresight input manifests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_checkpoint import DIAGNOSIS_CHECKPOINT_CONTRACT  # noqa: E402
from scan_foresight_snapshot import (  # noqa: E402
    FORESIGHT_INPUT_SNAPSHOT_CONTRACT,
    assert_foresight_input_snapshot_matches,
    build_foresight_input_snapshot,
)
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


def test_foresight_input_manifest_is_deterministic_and_contains_references_not_payload():
    checkpoint = _diagnosis_checkpoint()

    first = build_foresight_input_snapshot(
        run_id="RUN-FOR-001", attempt=1, diagnosis_checkpoint=checkpoint
    )
    second = build_foresight_input_snapshot(
        run_id="RUN-FOR-001", attempt=1, diagnosis_checkpoint=checkpoint
    )

    assert first == second
    assert first.contract_version == FORESIGHT_INPUT_SNAPSHOT_CONTRACT
    assert first.snapshot_ref == f"foresight-input://scan/{first.snapshot_id}"
    assert first.snapshot_id.startswith("FIS-")
    assert len(first.input_hash) == 64
    assert first.manifest["diagnosis_checkpoint_ref"]["output_hash"] == checkpoint.output_hash
    assert first.manifest["diagnosis_checkpoint_ref"]["checkpoint_id"] == checkpoint.checkpoint_id
    manifest_text = str(first.manifest)
    assert "cloud platform" not in manifest_text
    assert "diagnostic_summary" not in manifest_text
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
        "diagnosis_checkpoint_ref",
        "input_hash",
    }


def test_foresight_input_manifest_rejects_non_passed_and_non_diagnosis_checkpoints():
    with pytest.raises(ValueError, match="passed Diagnosis checkpoint"):
        build_foresight_input_snapshot(
            run_id="RUN-FOR-001",
            attempt=1,
            diagnosis_checkpoint=_diagnosis_checkpoint(status="validation_failed"),
        )

    with pytest.raises(ValueError, match="does not match the Foresight run"):
        build_foresight_input_snapshot(
            run_id="RUN-FOR-001",
            attempt=1,
            diagnosis_checkpoint=_diagnosis_checkpoint(run_id="RUN-OTHER"),
        )

    with pytest.raises(ValueError, match="passed Diagnosis checkpoint"):
        build_foresight_input_snapshot(
            run_id="RUN-FOR-001",
            attempt=1,
            diagnosis_checkpoint=_jury_checkpoint(),
        )


def test_tampered_or_different_diagnosis_checkpoint_cannot_match_the_manifest():
    checkpoint = _diagnosis_checkpoint()
    snapshot = build_foresight_input_snapshot(
        run_id="RUN-FOR-001", attempt=1, diagnosis_checkpoint=checkpoint
    )
    other = _diagnosis_checkpoint(output_hash="d" * 64)

    with pytest.raises(
        ForesightInputSnapshotIntegrityError, match="does not match the verified durable inputs"
    ):
        assert_foresight_input_snapshot_matches(snapshot, diagnosis_checkpoint=other)

    with pytest.raises(ForesightInputSnapshotIntegrityError, match="cannot be reconstructed"):
        assert_foresight_input_snapshot_matches(
            snapshot, diagnosis_checkpoint=_diagnosis_checkpoint(status="blocked")
        )


def test_module_does_not_call_foresight_generation_or_acquire_content():
    source = Path(_BACKEND, "scan_foresight_snapshot.py").read_text(encoding="utf-8")

    assert "analyze_intent" not in source
    assert "analyze_jury" not in source
    assert "strategic_foresight" not in source
    assert "foresight_harvester" not in source
    assert "scrape_strategic_content" not in source
    assert "x_ray_search" not in source
    assert "complete_step" not in source
    assert "POST /scan" not in source
    assert "kb_versions" not in source
