"""Hermetic contracts for the Jury fenced-commit helper."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_intent_checkpoint import INTENT_CHECKPOINT_CONTRACT  # noqa: E402
from scan_jury_adapter import JuryStageExecutor, analyze_jury_lanes  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_checkpoint import (  # noqa: E402
    JURY_CHECKPOINT_LANES,
    build_jury_checkpoint,
    build_jury_input_projection,
)
from scan_jury_commit import DIAGNOSIS_STAGE, commit_jury_stage  # noqa: E402
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import ScanCheckpoint, StageExecutionResult, StepLease  # noqa: E402
from scanner_two import FORENSIC_JURIES  # noqa: E402


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


def _intent_checkpoint(*, run_id="RUN-JURY-001"):
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


def _candidate(*, run_id="RUN-JURY-001", **overrides):
    values = dict(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://example.test/about/",
        recorded_at=datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc),
        cached_extract_text="Cached about-page extract",
        parser_route="html/main-text/v1",
        metadata={"discovery_label": "company_site"},
    )
    values.update(overrides)
    return prepare_jury_source_candidate(**values)


def _jury_retrieval(*, run_id="RUN-JURY-001"):
    return prepare_source_retrieval(
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=1,
        retrieval_origin="jury_web_acquisition",
        source_url="https://news.example.test/coverage/",
        retrieved_at=datetime(2026, 8, 28, 11, 5, tzinfo=timezone.utc),
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Jury-stage fetch</body></html>",
        extracted_text="Jury-stage fetch",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )


def _snapshot(candidates=None, retrievals=None, checkpoint=None):
    candidates = (_candidate(),) if candidates is None else candidates
    retrievals = (_jury_retrieval(),) if retrievals is None else retrievals
    checkpoint = _intent_checkpoint() if checkpoint is None else checkpoint
    return build_jury_input_snapshot(
        run_id="RUN-JURY-001",
        attempt=1,
        intent_checkpoint=checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )


def _lease(stage_name="jury_analysis"):
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    return StepLease(
        run_id="RUN-JURY-001",
        stage_name=stage_name,
        attempt=1,
        lease_owner="test-owner",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=30),
    )


def _prepared_result():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    checkpoint = _intent_checkpoint()
    snapshot = _snapshot(candidates, retrievals, checkpoint)

    class Loader:
        def load_verified_jury_inputs(self, *, run_id, attempt):
            return snapshot, checkpoint, list(candidates), list(retrievals)

    return JuryStageExecutor(Loader()).execute(_lease())


def test_commit_helper_queues_diagnosis_for_a_passed_proposal():
    result = _prepared_result()
    repository = RecordingRepository()
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)

    committed = commit_jury_stage(repository, _lease(), result, now=now)

    assert committed == result.checkpoint
    assert len(repository.complete_calls) == 1
    assert repository.complete_calls[0]["next_stage"] == DIAGNOSIS_STAGE
    assert repository.complete_calls[0]["checkpoint"] == result.checkpoint
    assert repository.complete_calls[0]["now"] == now


def test_identical_passed_checkpoint_replay_does_not_complete_again():
    result = _prepared_result()
    repository = RecordingRepository(existing=result.checkpoint)

    committed = commit_jury_stage(repository, _lease(), result)

    assert committed == result.checkpoint
    assert repository.complete_calls == []


def test_different_existing_checkpoint_is_rejected():
    result = _prepared_result()
    other = ScanCheckpoint(
        checkpoint_id="CP-OTHER",
        run_id="RUN-JURY-001",
        stage_name="jury_analysis",
        attempt=1,
        contract_version=result.checkpoint.contract_version,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result=result.checkpoint.gate_result,
        snapshot=result.checkpoint.snapshot,
    )
    repository = RecordingRepository(existing=other)

    with pytest.raises(ValueError, match="different Jury checkpoint"):
        commit_jury_stage(repository, _lease(), result)
    assert repository.complete_calls == []


def test_malformed_or_blocked_proposal_cannot_be_committed():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    checkpoint = _intent_checkpoint()
    projection = build_jury_input_projection(
        _snapshot(candidates, retrievals, checkpoint),
        intent_checkpoint=checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )
    failed = build_jury_checkpoint(projection, jury_result={"findings": []})
    result = StageExecutionResult(
        output_hash=failed.output_hash,
        checkpoint_ref=failed.checkpoint_ref,
        gate_result=failed.gate_result,
        next_stage=None,
        checkpoint=failed,
    )
    repository = RecordingRepository()

    with pytest.raises(ValueError, match="passed Jury gate"):
        commit_jury_stage(repository, _lease(), result)
    with pytest.raises(ValueError, match="cannot complete stage"):
        commit_jury_stage(repository, _lease(stage_name="intent"), _prepared_result())
    assert repository.complete_calls == []
    assert failed.gate_result["status"] == "validation_failed"


def test_jury_packet_wrap_fixture_is_unchanged():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    checkpoint = _intent_checkpoint()
    snapshot = _snapshot(candidates, retrievals, checkpoint)
    projection = build_jury_input_projection(
        snapshot,
        intent_checkpoint=checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )
    wrapped = analyze_jury_lanes(projection)
    result = _prepared_result()

    assert result.checkpoint == build_jury_checkpoint(projection, jury_result=wrapped)
    assert tuple(FORENSIC_JURIES) == JURY_CHECKPOINT_LANES
    assert set(wrapped) == {"juries"}
    assert [lane["jury"] for lane in wrapped["juries"]] == list(JURY_CHECKPOINT_LANES)


def test_commit_module_does_not_register_or_switch_live_paths():
    source = Path(_BACKEND, "scan_jury_commit.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "FastAPI" not in source
    assert "SCAN_SUPERVISOR" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "claim_next_step" not in source
    assert "foresight" not in source.lower()
    assert "strategic_synthesis" not in source
    assert "verification" not in source.lower()
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
