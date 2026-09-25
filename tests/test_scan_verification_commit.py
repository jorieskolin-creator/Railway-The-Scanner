"""Hermetic contracts for the Verification fenced-commit helper."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease  # noqa: E402
from scan_synthesis_checkpoint import SYNTHESIS_CHECKPOINT_CONTRACT, empty_synthesis_result  # noqa: E402
from scan_verification_adapter import (  # noqa: E402
    VerificationStageExecutor,
    analyze_verification_from_synthesis,
)
from scan_verification_checkpoint import (  # noqa: E402
    build_verification_checkpoint,
    build_verification_input_projection,
    empty_verification_result,
)
from scan_verification_commit import commit_verification_stage  # noqa: E402
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402


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


def _synthesis_checkpoint():
    return ScanCheckpoint(
        checkpoint_id="CP-" + "S" * 32,
        run_id="RUN-VER-001",
        stage_name="strategic_synthesis",
        attempt=1,
        contract_version=SYNTHESIS_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result={
            "gate": SYNTHESIS_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        },
        snapshot={
            "contract": SYNTHESIS_CHECKPOINT_CONTRACT,
            "synthesis_result": empty_synthesis_result(),
        },
    )


def _lease(stage_name="verification"):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    return StepLease(
        run_id="RUN-VER-001",
        stage_name=stage_name,
        attempt=1,
        lease_owner="test-owner",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=30),
    )


def _prepared_result():
    checkpoint = _synthesis_checkpoint()
    snapshot = build_verification_input_snapshot(
        run_id="RUN-VER-001",
        attempt=1,
        synthesis_checkpoint=checkpoint,
    )

    class Loader:
        def load_verified_verification_inputs(self, *, run_id, attempt):
            return snapshot, checkpoint

    return VerificationStageExecutor(Loader()).execute(_lease())


def test_commit_helper_completes_verification_without_a_successor():
    result = _prepared_result()
    repository = RecordingRepository()
    now = datetime(2026, 9, 25, 13, 0, tzinfo=timezone.utc)

    committed = commit_verification_stage(repository, _lease(), result, now=now)

    assert committed == result.checkpoint
    assert len(repository.complete_calls) == 1
    assert repository.complete_calls[0]["next_stage"] is None
    assert repository.complete_calls[0]["checkpoint"] == result.checkpoint
    assert repository.complete_calls[0]["now"] == now


def test_identical_passed_checkpoint_replay_does_not_complete_again():
    result = _prepared_result()
    repository = RecordingRepository(existing=result.checkpoint)

    committed = commit_verification_stage(repository, _lease(), result)

    assert committed == result.checkpoint
    assert repository.complete_calls == []


def test_different_existing_checkpoint_is_rejected():
    result = _prepared_result()
    other = ScanCheckpoint(
        checkpoint_id="CP-OTHER",
        run_id="RUN-VER-001",
        stage_name="verification",
        attempt=1,
        contract_version=result.checkpoint.contract_version,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result=result.checkpoint.gate_result,
        snapshot=result.checkpoint.snapshot,
    )
    repository = RecordingRepository(existing=other)

    with pytest.raises(ValueError, match="different Verification checkpoint"):
        commit_verification_stage(repository, _lease(), result)
    assert repository.complete_calls == []


def test_malformed_proposal_cannot_be_committed():
    checkpoint = _synthesis_checkpoint()
    snapshot = build_verification_input_snapshot(
        run_id="RUN-VER-001",
        attempt=1,
        synthesis_checkpoint=checkpoint,
    )
    projection = build_verification_input_projection(snapshot, synthesis_checkpoint=checkpoint)
    failed = build_verification_checkpoint(
        projection, verification_result={"publication_status": "publish"}
    )
    result = StageExecutionResult(
        output_hash=failed.output_hash,
        checkpoint_ref=failed.checkpoint_ref,
        gate_result=failed.gate_result,
        next_stage=None,
        checkpoint=failed,
    )
    repository = RecordingRepository()

    with pytest.raises(ValueError, match="passed Verification gate"):
        commit_verification_stage(repository, _lease(), result)
    with pytest.raises(ValueError, match="cannot complete stage"):
        commit_verification_stage(
            repository, _lease(stage_name="strategic_synthesis"), _prepared_result()
        )
    queued = _prepared_result()
    queued = StageExecutionResult(
        output_hash=queued.output_hash,
        checkpoint_ref=queued.checkpoint_ref,
        gate_result=queued.gate_result,
        next_stage="publication",
        checkpoint=queued.checkpoint,
    )
    with pytest.raises(ValueError, match="cannot queue a successor"):
        commit_verification_stage(repository, _lease(), queued)
    assert repository.complete_calls == []
    assert failed.gate_result["status"] == "validation_failed"


def test_verification_wrap_fixture_is_unchanged():
    checkpoint = _synthesis_checkpoint()
    snapshot = build_verification_input_snapshot(
        run_id="RUN-VER-001",
        attempt=1,
        synthesis_checkpoint=checkpoint,
    )
    projection = build_verification_input_projection(snapshot, synthesis_checkpoint=checkpoint)
    wrapped = analyze_verification_from_synthesis(projection)
    result = _prepared_result()

    assert result.checkpoint == build_verification_checkpoint(
        projection, verification_result=wrapped
    )
    assert wrapped == empty_verification_result()
    assert set(wrapped) == {"verification_record"}


def test_commit_module_does_not_register_publish_or_queue_a_successor():
    source = Path(_BACKEND, "scan_verification_commit.py").read_text(encoding="utf-8")

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
    assert "publication" not in source
    assert "Blob" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "next_stage=None" in source
