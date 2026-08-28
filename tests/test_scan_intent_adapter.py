"""Phase 3 contracts for the prepare-only Intent adapter."""

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
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import StepLease  # noqa: E402
from scanner_one import analyze_intent  # noqa: E402


class RecordingLoader:
    def __init__(self, snapshot, materials, retrievals):
        self.snapshot = snapshot
        self.materials = list(materials)
        self.retrievals = list(retrievals)
        self.calls = []

    def load_verified_intent_inputs(self, *, run_id, attempt):
        self.calls.append((run_id, attempt))
        return self.snapshot, list(self.materials), list(self.retrievals)


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


def test_adapter_scores_verified_snapshot_text_without_a_state_mutation_method():
    materials = _materials()
    retrievals = _retrievals()
    snapshot = _snapshot(materials, retrievals)
    loader = RecordingLoader(snapshot, materials, retrievals)
    projection = build_intent_input_projection(snapshot, materials=materials, retrievals=retrievals)

    result = IntentStageExecutor(loader).execute(_lease())

    assert loader.calls == [("RUN-INTENT-001", 1)]
    assert result.checkpoint is not None
    assert result.checkpoint == build_intent_checkpoint(
        projection, intent_result=analyze_intent(projection.concatenated_text)
    )
    assert result.next_stage == "jury_analysis"
    assert result.gate_result["status"] == "passed"
    assert result.output_hash == result.checkpoint.output_hash
    assert result.checkpoint_ref == result.checkpoint.checkpoint_ref
    assert not hasattr(IntentStageExecutor, "complete_step")
    assert not hasattr(IntentStageExecutor, "claim_next_step")
    assert not hasattr(IntentStageExecutor, "persist")


def test_adapter_rejects_a_non_intent_stage():
    materials = _materials()
    retrievals = _retrievals()
    loader = RecordingLoader(_snapshot(materials, retrievals), materials, retrievals)

    with pytest.raises(ValueError, match="cannot execute"):
        IntentStageExecutor(loader).execute(_lease(stage_name="material_ingestion"))
    assert loader.calls == []


def test_adapter_does_not_import_acquisition_or_live_scan_paths():
    source = Path(_BACKEND, "scan_intent_adapter.py").read_text(encoding="utf-8")

    assert "scrape_strategic_content" not in source
    assert "scan_ingress" not in source
    assert "complete_step" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
