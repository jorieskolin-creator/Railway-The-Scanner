"""Phase 3 contracts for the prepare-only Jury adapter."""

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
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import ScanCheckpoint, StepLease  # noqa: E402
from scanner_two import FORENSIC_JURIES, _prioritize_jury_packets  # noqa: E402


class RecordingLoader:
    def __init__(self, snapshot, intent_checkpoint, candidates, retrievals):
        self.snapshot = snapshot
        self.intent_checkpoint = intent_checkpoint
        self.candidates = list(candidates)
        self.retrievals = list(retrievals)
        self.calls = []

    def load_verified_jury_inputs(self, *, run_id, attempt):
        self.calls.append((run_id, attempt))
        return (
            self.snapshot,
            self.intent_checkpoint,
            list(self.candidates),
            list(self.retrievals),
        )


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


def test_adapter_wraps_verified_packets_without_a_state_mutation_method():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    checkpoint = _intent_checkpoint()
    snapshot = _snapshot(candidates, retrievals, checkpoint)
    loader = RecordingLoader(snapshot, checkpoint, candidates, retrievals)
    projection = build_jury_input_projection(
        snapshot,
        intent_checkpoint=checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )

    result = JuryStageExecutor(loader).execute(_lease())

    assert loader.calls == [("RUN-JURY-001", 1)]
    assert result.checkpoint is not None
    assert result.checkpoint == build_jury_checkpoint(
        projection, jury_result=analyze_jury_lanes(projection)
    )
    assert result.next_stage == "diagnosis"
    assert result.gate_result["status"] == "passed"
    assert result.output_hash == result.checkpoint.output_hash
    assert result.checkpoint_ref == result.checkpoint.checkpoint_ref
    assert [lane["jury"] for lane in result.checkpoint.snapshot["jury_result"]["juries"]] == list(
        JURY_CHECKPOINT_LANES
    )
    assert tuple(FORENSIC_JURIES) == JURY_CHECKPOINT_LANES
    assert not hasattr(JuryStageExecutor, "complete_step")
    assert not hasattr(JuryStageExecutor, "claim_next_step")
    assert not hasattr(JuryStageExecutor, "persist")


def test_adapter_ranks_packets_with_the_existing_priority_helper():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    checkpoint = _intent_checkpoint()
    snapshot = _snapshot(candidates, retrievals, checkpoint)
    loader = RecordingLoader(snapshot, checkpoint, candidates, retrievals)
    projection = build_jury_input_projection(
        snapshot,
        intent_checkpoint=checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )
    recorded = []

    def recording_lane(jury_name, packets):
        recorded.append((jury_name, [packet["source_id"] for packet in packets]))
        return {
            "jury": jury_name,
            "findings": [],
            "rejected_evidence": [],
            "contradictions": [],
            "missing_coverage": [],
            "confidence_score": 0,
            "reality_score_adjustment": 0,
        }

    result = JuryStageExecutor(
        loader,
        analyze_jury=lambda item: analyze_jury_lanes(item, analyze_lane=recording_lane),
    ).execute(_lease())

    assert result.gate_result["status"] == "passed"
    assert [name for name, _ in recorded] == list(FORENSIC_JURIES)
    for jury_name, source_ids in recorded:
        expected = [
            packet["source_id"]
            for packet in _prioritize_jury_packets(jury_name, list(projection.packets))
        ]
        assert source_ids == expected


def test_adapter_rejects_a_non_jury_stage():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    checkpoint = _intent_checkpoint()
    loader = RecordingLoader(
        _snapshot(candidates, retrievals, checkpoint),
        checkpoint,
        candidates,
        retrievals,
    )

    with pytest.raises(ValueError, match="cannot execute"):
        JuryStageExecutor(loader).execute(_lease(stage_name="intent"))
    assert loader.calls == []


def test_malformed_injected_result_cannot_queue_diagnosis():
    candidates = (_candidate(),)
    retrievals = (_jury_retrieval(),)
    checkpoint = _intent_checkpoint()
    loader = RecordingLoader(
        _snapshot(candidates, retrievals, checkpoint),
        checkpoint,
        candidates,
        retrievals,
    )

    result = JuryStageExecutor(loader, analyze_jury=lambda _projection: {"findings": []}).execute(
        _lease()
    )

    assert result.gate_result["status"] == "validation_failed"
    assert result.next_stage is None
    assert result.checkpoint is not None
    assert "jury_result" not in result.checkpoint.snapshot


def test_adapter_does_not_import_acquisition_or_live_scan_paths():
    source = Path(_BACKEND, "scan_jury_adapter.py").read_text(encoding="utf-8")

    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "scan_ingress" not in source
    assert "complete_step" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
