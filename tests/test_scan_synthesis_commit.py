"""Hermetic contracts for the Strategic Synthesis fenced-commit helper."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_foresight_checkpoint import FORESIGHT_CHECKPOINT_CONTRACT, empty_foresight_result  # noqa: E402
from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease  # noqa: E402
from scan_synthesis_adapter import SynthesisStageExecutor, analyze_synthesis_from_foresight  # noqa: E402
from scan_synthesis_checkpoint import (  # noqa: E402
    build_synthesis_checkpoint,
    build_synthesis_input_projection,
    empty_synthesis_result,
)
from scan_synthesis_commit import VERIFICATION_STAGE, commit_synthesis_stage  # noqa: E402
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402


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


def _foresight_checkpoint(*, run_id="RUN-SYN-001"):
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
            "foresight_result": empty_foresight_result(),
        },
    )


def _snapshot(checkpoint=None):
    checkpoint = _foresight_checkpoint() if checkpoint is None else checkpoint
    return build_synthesis_input_snapshot(
        run_id="RUN-SYN-001",
        attempt=1,
        foresight_checkpoint=checkpoint,
    )


def _lease(stage_name="strategic_synthesis"):
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    return StepLease(
        run_id="RUN-SYN-001",
        stage_name=stage_name,
        attempt=1,
        lease_owner="test-owner",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=30),
    )


def _prepared_result():
    checkpoint = _foresight_checkpoint()
    snapshot = _snapshot(checkpoint)

    class Loader:
        def load_verified_synthesis_inputs(self, *, run_id, attempt):
            return snapshot, checkpoint

    return SynthesisStageExecutor(Loader()).execute(_lease())


def test_commit_helper_queues_verification_for_a_passed_proposal():
    result = _prepared_result()
    repository = RecordingRepository()
    now = datetime(2026, 9, 4, 13, 0, tzinfo=timezone.utc)

    committed = commit_synthesis_stage(repository, _lease(), result, now=now)

    assert committed == result.checkpoint
    assert len(repository.complete_calls) == 1
    assert repository.complete_calls[0]["next_stage"] == VERIFICATION_STAGE
    assert repository.complete_calls[0]["checkpoint"] == result.checkpoint
    assert repository.complete_calls[0]["now"] == now


def test_identical_passed_checkpoint_replay_does_not_complete_again():
    result = _prepared_result()
    repository = RecordingRepository(existing=result.checkpoint)

    committed = commit_synthesis_stage(repository, _lease(), result)

    assert committed == result.checkpoint
    assert repository.complete_calls == []


def test_different_existing_checkpoint_is_rejected():
    result = _prepared_result()
    other = ScanCheckpoint(
        checkpoint_id="CP-OTHER",
        run_id="RUN-SYN-001",
        stage_name="strategic_synthesis",
        attempt=1,
        contract_version=result.checkpoint.contract_version,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result=result.checkpoint.gate_result,
        snapshot=result.checkpoint.snapshot,
    )
    repository = RecordingRepository(existing=other)

    with pytest.raises(ValueError, match="different Synthesis checkpoint"):
        commit_synthesis_stage(repository, _lease(), result)
    assert repository.complete_calls == []


def test_malformed_or_blocked_proposal_cannot_be_committed():
    checkpoint = _foresight_checkpoint()
    snapshot = _snapshot(checkpoint)
    projection = build_synthesis_input_projection(snapshot, foresight_checkpoint=checkpoint)
    failed = build_synthesis_checkpoint(projection, synthesis_result={"commercial_pitch": "Buy now."})
    result = StageExecutionResult(
        output_hash=failed.output_hash,
        checkpoint_ref=failed.checkpoint_ref,
        gate_result=failed.gate_result,
        next_stage=None,
        checkpoint=failed,
    )
    repository = RecordingRepository()

    with pytest.raises(ValueError, match="passed Synthesis gate"):
        commit_synthesis_stage(repository, _lease(), result)
    with pytest.raises(ValueError, match="cannot complete stage"):
        commit_synthesis_stage(repository, _lease(stage_name="foresight"), _prepared_result())
    assert repository.complete_calls == []
    assert failed.gate_result["status"] == "validation_failed"


def test_synthesis_wrap_fixture_is_unchanged():
    checkpoint = _foresight_checkpoint()
    snapshot = _snapshot(checkpoint)
    projection = build_synthesis_input_projection(snapshot, foresight_checkpoint=checkpoint)
    wrapped = analyze_synthesis_from_foresight(projection)
    result = _prepared_result()

    assert result.checkpoint == build_synthesis_checkpoint(projection, synthesis_result=wrapped)
    assert wrapped == empty_synthesis_result()
    assert set(wrapped) == {"strategic_synthesis"}


def test_commit_module_does_not_register_or_switch_live_paths():
    source = Path(_BACKEND, "scan_synthesis_commit.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "FastAPI" not in source
    assert "SCAN_SUPERVISOR" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "claim_next_step" not in source
    assert "generate_foresight" not in source
    assert "foresight_harvester" not in source
    assert "kb_versions" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "VerificationStage" not in source
    assert "scan_verification" not in source
    assert VERIFICATION_STAGE in source
