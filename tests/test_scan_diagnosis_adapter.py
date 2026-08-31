"""Phase 3 contracts for the prepare-only Diagnosis adapter."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_adapter import (  # noqa: E402
    DiagnosisStageExecutor,
    analyze_diagnosis_from_jury,
)
from scan_diagnosis_checkpoint import (  # noqa: E402
    build_diagnosis_checkpoint,
    build_diagnosis_input_projection,
    empty_diagnosis_result,
)
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_jury_checkpoint import JURY_CHECKPOINT_CONTRACT  # noqa: E402
from scan_supervisor import ScanCheckpoint, StepLease  # noqa: E402


class RecordingLoader:
    def __init__(self, snapshot, jury_checkpoint):
        self.snapshot = snapshot
        self.jury_checkpoint = jury_checkpoint
        self.calls = []

    def load_verified_diagnosis_inputs(self, *, run_id, attempt):
        self.calls.append((run_id, attempt))
        return self.snapshot, self.jury_checkpoint


def _jury_checkpoint(*, run_id="RUN-DIAG-001"):
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
                        "findings": [],
                        "rejected_evidence": [],
                        "contradictions": [],
                        "missing_coverage": [],
                        "confidence_score": 0,
                        "reality_score_adjustment": 0,
                    }
                ]
            },
        },
    )


def _snapshot(checkpoint=None):
    checkpoint = _jury_checkpoint() if checkpoint is None else checkpoint
    return build_diagnosis_input_snapshot(
        run_id="RUN-DIAG-001",
        attempt=1,
        jury_checkpoint=checkpoint,
    )


def _lease(stage_name="diagnosis"):
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    return StepLease(
        run_id="RUN-DIAG-001",
        stage_name=stage_name,
        attempt=1,
        lease_owner="test-owner",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=30),
    )


def test_adapter_wraps_verified_jury_output_without_a_state_mutation_method():
    checkpoint = _jury_checkpoint()
    snapshot = _snapshot(checkpoint)
    loader = RecordingLoader(snapshot, checkpoint)
    projection = build_diagnosis_input_projection(snapshot, jury_checkpoint=checkpoint)

    result = DiagnosisStageExecutor(loader).execute(_lease())

    assert loader.calls == [("RUN-DIAG-001", 1)]
    assert result.checkpoint is not None
    assert result.checkpoint == build_diagnosis_checkpoint(
        projection, diagnosis_result=analyze_diagnosis_from_jury(projection)
    )
    assert result.next_stage == "foresight"
    assert result.gate_result["status"] == "passed"
    assert result.checkpoint.snapshot["diagnosis_result"] == empty_diagnosis_result()
    assert result.output_hash == result.checkpoint.output_hash
    assert result.checkpoint_ref == result.checkpoint.checkpoint_ref
    assert not hasattr(DiagnosisStageExecutor, "complete_step")
    assert not hasattr(DiagnosisStageExecutor, "claim_next_step")
    assert not hasattr(DiagnosisStageExecutor, "persist")


def test_adapter_rejects_a_non_diagnosis_stage():
    checkpoint = _jury_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    with pytest.raises(ValueError, match="cannot execute"):
        DiagnosisStageExecutor(loader).execute(_lease(stage_name="jury_analysis"))
    assert loader.calls == []


def test_malformed_injected_result_cannot_queue_foresight():
    checkpoint = _jury_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    result = DiagnosisStageExecutor(
        loader,
        analyze_diagnosis=lambda _projection: {"diagnostic_summary": "partial"},
    ).execute(_lease())

    assert result.gate_result["status"] == "validation_failed"
    assert result.next_stage is None
    assert result.checkpoint is not None
    assert "diagnosis_result" not in result.checkpoint.snapshot


def test_adapter_does_not_import_acquisition_or_live_scan_paths():
    source = Path(_BACKEND, "scan_diagnosis_adapter.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "scan_ingress" not in source
    assert "complete_step" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "generate_foresight" not in source
    assert "strategic_synthesis" not in source
