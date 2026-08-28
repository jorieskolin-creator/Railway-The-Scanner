"""Hermetic contracts for the Intent fenced-commit helper."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_intent_adapter import IntentStageExecutor  # noqa: E402
from scan_intent_checkpoint import build_intent_checkpoint, build_intent_input_projection  # noqa: E402
from scan_intent_commit import JURY_ANALYSIS_STAGE, commit_intent_stage  # noqa: E402
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease  # noqa: E402
from scanner_one import analyze_intent  # noqa: E402


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


def _materials():
    return prepare_scan_materials(
        run_id="RUN-INTENT-001",
        report_text="Submitted company context about customer intimacy and autonomy.",
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
    ).materials


def _retrievals():
    return (
        prepare_source_retrieval(
            run_id="RUN-INTENT-001",
            stage_name="intent",
            attempt=1,
            retrieval_origin="intent_web_acquisition",
            source_url="https://example.test/strategy/",
            retrieved_at=datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc),
            http_status=200,
            content_type="text/html",
            response_content=b"<html><body>Acquired page</body></html>",
            extracted_text="Acquired page",
            parser_route="html/main-text/v1",
            readability_status="readable",
            retrieval_outcome="succeeded",
        ),
    )


def _snapshot(materials=None, retrievals=None):
    materials = _materials() if materials is None else materials
    retrievals = _retrievals() if retrievals is None else retrievals
    return build_intent_input_snapshot(
        run_id="RUN-INTENT-001",
        attempt=1,
        materials=materials,
        retrievals=retrievals,
    )


def _lease(stage_name="intent"):
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    return StepLease(
        run_id="RUN-INTENT-001",
        stage_name=stage_name,
        attempt=1,
        lease_owner="test-owner",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=30),
    )


def _prepared_result():
    materials = _materials()
    retrievals = _retrievals()
    snapshot = _snapshot(materials, retrievals)

    class Loader:
        def load_verified_intent_inputs(self, *, run_id, attempt):
            return snapshot, list(materials), list(retrievals)

    return IntentStageExecutor(Loader()).execute(_lease())


def test_commit_helper_queues_jury_analysis_for_a_passed_proposal():
    result = _prepared_result()
    repository = RecordingRepository()
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)

    committed = commit_intent_stage(repository, _lease(), result, now=now)

    assert committed == result.checkpoint
    assert len(repository.complete_calls) == 1
    assert repository.complete_calls[0]["next_stage"] == JURY_ANALYSIS_STAGE
    assert repository.complete_calls[0]["checkpoint"] == result.checkpoint
    assert repository.complete_calls[0]["now"] == now


def test_identical_passed_checkpoint_replay_does_not_complete_again():
    result = _prepared_result()
    repository = RecordingRepository(existing=result.checkpoint)

    committed = commit_intent_stage(repository, _lease(), result)

    assert committed == result.checkpoint
    assert repository.complete_calls == []


def test_different_existing_checkpoint_is_rejected():
    result = _prepared_result()
    other = ScanCheckpoint(
        checkpoint_id="CP-OTHER",
        run_id="RUN-INTENT-001",
        stage_name="intent",
        attempt=1,
        contract_version=result.checkpoint.contract_version,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result=result.checkpoint.gate_result,
        snapshot=result.checkpoint.snapshot,
    )
    repository = RecordingRepository(existing=other)

    with pytest.raises(ValueError, match="different Intent checkpoint"):
        commit_intent_stage(repository, _lease(), result)
    assert repository.complete_calls == []


def test_malformed_or_blocked_proposal_cannot_be_committed():
    projection = build_intent_input_projection(
        _snapshot(), materials=_materials(), retrievals=_retrievals()
    )
    failed = build_intent_checkpoint(projection, intent_result={"score": 1})
    result = StageExecutionResult(
        output_hash=failed.output_hash,
        checkpoint_ref=failed.checkpoint_ref,
        gate_result=failed.gate_result,
        next_stage=None,
        checkpoint=failed,
    )
    repository = RecordingRepository()

    with pytest.raises(ValueError, match="passed Intent gate"):
        commit_intent_stage(repository, _lease(), result)
    with pytest.raises(ValueError, match="cannot complete stage"):
        commit_intent_stage(repository, _lease(stage_name="material_ingestion"), _prepared_result())
    assert repository.complete_calls == []
    assert failed.gate_result["status"] == "validation_failed"


def test_analyze_intent_scoring_fixture_is_unchanged():
    materials = _materials()
    retrievals = _retrievals()
    snapshot = _snapshot(materials, retrievals)
    projection = build_intent_input_projection(snapshot, materials=materials, retrievals=retrievals)
    scored = analyze_intent(projection.concatenated_text)
    result = _prepared_result()

    assert result.checkpoint == build_intent_checkpoint(projection, intent_result=scored)
    assert set(scored) == {"score", "breakdown", "metrics"}
    assert isinstance(scored["score"], int)
    assert isinstance(scored["breakdown"], list)
    assert set(scored["metrics"]) == {
        "friction_index",
        "vibrancy_score",
        "total_good_mentions",
        "total_bad_mentions",
    }


def test_commit_module_does_not_register_or_switch_live_paths():
    source = Path(_BACKEND, "scan_intent_commit.py").read_text(encoding="utf-8")

    assert "analyze_intent" not in source
    assert "scrape_strategic_content" not in source
    assert "FastAPI" not in source
    assert "SCAN_SUPERVISOR" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "claim_next_step" not in source
