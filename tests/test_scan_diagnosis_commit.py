"""Hermetic contracts for the Diagnosis fenced-commit helper."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_adapter import DiagnosisStageExecutor, analyze_diagnosis_from_jury  # noqa: E402
from scan_diagnosis_checkpoint import (  # noqa: E402
    build_diagnosis_checkpoint,
    build_diagnosis_input_projection,
    empty_diagnosis_result,
)
from scan_diagnosis_commit import FORESIGHT_STAGE, commit_diagnosis_stage  # noqa: E402
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_jury_checkpoint import JURY_CHECKPOINT_CONTRACT  # noqa: E402
from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease  # noqa: E402


class RecordingRepository:
    def __init__(self, existing=None):
        self.existing = existing
        self.complete_calls = []

    def load_checkpoint(self, *, run_id, stage_name, attempt):
        self.last_load = (run_id, stage_name, attempt)
        return self.existing

    def complete_step_with_checkpoint(self, lease, *, checkpoint, next_stage, now=None):
        self.complete_calls.append(
            {
                "lease": lease,
                "checkpoint": checkpoint,
                "next_stage": next_stage,
                "now": now,
            }
        )
        self.existing = checkpoint


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


def _prepared_result():
    checkpoint = _jury_checkpoint()
    snapshot = _snapshot(checkpoint)

    class Loader:
        def load_verified_diagnosis_inputs(self, *, run_id, attempt):
            return snapshot, checkpoint

    return DiagnosisStageExecutor(Loader()).execute(_lease())


def test_commit_helper_queues_foresight_for_a_passed_proposal():
    result = _prepared_result()
    repository = RecordingRepository()
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

    committed = commit_diagnosis_stage(repository, _lease(), result, now=now)

    assert committed == result.checkpoint
    assert len(repository.complete_calls) == 1
    assert repository.complete_calls[0]["next_stage"] == FORESIGHT_STAGE
    assert repository.complete_calls[0]["checkpoint"] == result.checkpoint
    assert repository.complete_calls[0]["now"] == now


def test_identical_passed_checkpoint_replay_does_not_complete_again():
    result = _prepared_result()
    repository = RecordingRepository(existing=result.checkpoint)

    committed = commit_diagnosis_stage(repository, _lease(), result)

    assert committed == result.checkpoint
    assert repository.complete_calls == []


def test_different_existing_checkpoint_is_rejected():
    result = _prepared_result()
    other = ScanCheckpoint(
        checkpoint_id="CP-OTHER",
        run_id="RUN-DIAG-001",
        stage_name="diagnosis",
        attempt=1,
        contract_version=result.checkpoint.contract_version,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result=result.checkpoint.gate_result,
        snapshot=result.checkpoint.snapshot,
    )
    repository = RecordingRepository(existing=other)

    with pytest.raises(ValueError, match="different Diagnosis checkpoint"):
        commit_diagnosis_stage(repository, _lease(), result)
    assert repository.complete_calls == []


def test_malformed_or_blocked_proposal_cannot_be_committed():
    checkpoint = _jury_checkpoint()
    snapshot = _snapshot(checkpoint)
    projection = build_diagnosis_input_projection(snapshot, jury_checkpoint=checkpoint)
    failed = build_diagnosis_checkpoint(projection, diagnosis_result={"diagnostic_summary": "partial"})
    result = StageExecutionResult(
        output_hash=failed.output_hash,
        checkpoint_ref=failed.checkpoint_ref,
        gate_result=failed.gate_result,
        next_stage=None,
        checkpoint=failed,
    )
    repository = RecordingRepository()

    with pytest.raises(ValueError, match="passed Diagnosis gate"):
        commit_diagnosis_stage(repository, _lease(), result)
    with pytest.raises(ValueError, match="cannot complete stage"):
        commit_diagnosis_stage(repository, _lease(stage_name="jury_analysis"), _prepared_result())
    assert repository.complete_calls == []
    assert failed.gate_result["status"] == "validation_failed"


def test_diagnosis_wrap_fixture_is_unchanged():
    checkpoint = _jury_checkpoint()
    snapshot = _snapshot(checkpoint)
    projection = build_diagnosis_input_projection(snapshot, jury_checkpoint=checkpoint)
    wrapped = analyze_diagnosis_from_jury(projection)
    result = _prepared_result()

    assert result.checkpoint == build_diagnosis_checkpoint(projection, diagnosis_result=wrapped)
    assert wrapped == empty_diagnosis_result()
    assert set(wrapped) == {
        "diagnostic_summary",
        "evidence_based_risks",
        "reality_score",
        "archetype",
    }


def test_commit_module_does_not_register_or_switch_live_paths():
    source = Path(_BACKEND, "scan_diagnosis_commit.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "FastAPI" not in source
    assert "SCAN_SUPERVISOR" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "claim_next_step" not in source
    assert "generate_foresight" not in source
    assert "strategic_synthesis" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
