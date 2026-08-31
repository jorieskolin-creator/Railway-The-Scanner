"""Contracts for immutable, evidence-neutral Diagnosis input manifests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_snapshot import (  # noqa: E402
    DIAGNOSIS_INPUT_SNAPSHOT_CONTRACT,
    assert_diagnosis_input_snapshot_matches,
    build_diagnosis_input_snapshot,
)
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


def test_diagnosis_input_manifest_is_deterministic_and_contains_references_not_payload():
    checkpoint = _jury_checkpoint()

    first = build_diagnosis_input_snapshot(
        run_id="RUN-DIAG-001", attempt=1, jury_checkpoint=checkpoint
    )
    second = build_diagnosis_input_snapshot(
        run_id="RUN-DIAG-001", attempt=1, jury_checkpoint=checkpoint
    )

    assert first == second
    assert first.contract_version == DIAGNOSIS_INPUT_SNAPSHOT_CONTRACT
    assert first.snapshot_ref == f"diagnosis-input://scan/{first.snapshot_id}"
    assert first.snapshot_id.startswith("DIS-")
    assert len(first.input_hash) == 64
    assert first.manifest["jury_checkpoint_ref"]["output_hash"] == checkpoint.output_hash
    assert first.manifest["jury_checkpoint_ref"]["checkpoint_id"] == checkpoint.checkpoint_id
    manifest_text = str(first.manifest)
    assert "cloud platform" not in manifest_text
    assert "source_quote" not in manifest_text
    assert "jury_result" not in manifest_text
    assert "intent_result" not in manifest_text
    assert "extracted_text" not in manifest_text
    assert "claim_support" not in manifest_text
    assert "foresight_eligible" not in manifest_text
    assert "candidate_refs" not in first.manifest
    assert "retrieval_refs" not in first.manifest
    assert set(first.manifest) == {
        "contract",
        "run_id",
        "stage",
        "attempt",
        "jury_checkpoint_ref",
        "input_hash",
    }


def test_diagnosis_input_manifest_rejects_non_passed_and_non_jury_checkpoints():
    with pytest.raises(ValueError, match="passed Jury checkpoint"):
        build_diagnosis_input_snapshot(
            run_id="RUN-DIAG-001",
            attempt=1,
            jury_checkpoint=_jury_checkpoint(status="validation_failed"),
        )

    with pytest.raises(ValueError, match="does not match the Diagnosis run"):
        build_diagnosis_input_snapshot(
            run_id="RUN-DIAG-001",
            attempt=1,
            jury_checkpoint=_jury_checkpoint(run_id="RUN-OTHER"),
        )

    with pytest.raises(ValueError, match="passed Jury checkpoint"):
        build_diagnosis_input_snapshot(
            run_id="RUN-DIAG-001",
            attempt=1,
            jury_checkpoint=_intent_checkpoint(),
        )


def test_tampered_or_different_jury_checkpoint_cannot_match_the_manifest():
    checkpoint = _jury_checkpoint()
    snapshot = build_diagnosis_input_snapshot(
        run_id="RUN-DIAG-001", attempt=1, jury_checkpoint=checkpoint
    )
    other = _jury_checkpoint(output_hash="d" * 64)

    with pytest.raises(
        DiagnosisInputSnapshotIntegrityError, match="does not match the verified durable inputs"
    ):
        assert_diagnosis_input_snapshot_matches(snapshot, jury_checkpoint=other)

    with pytest.raises(DiagnosisInputSnapshotIntegrityError, match="cannot be reconstructed"):
        assert_diagnosis_input_snapshot_matches(
            snapshot, jury_checkpoint=_jury_checkpoint(status="blocked")
        )


def test_module_does_not_call_diagnosis_or_acquire_content():
    source = Path(_BACKEND, "scan_diagnosis_snapshot.py").read_text(encoding="utf-8")

    assert "analyze_intent" not in source
    assert "analyze_jury" not in source
    assert "scrape_strategic_content" not in source
    assert "x_ray_search" not in source
    assert "complete_step" not in source
    assert "foresight" not in source.lower()
    assert "POST /scan" not in source
