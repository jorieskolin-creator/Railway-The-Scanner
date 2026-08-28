"""Phase 3 contracts for the prepare-only material-ingestion adapter."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_materials import prepare_scan_materials  # noqa: E402
from scan_stage_executor import MaterialIngestionStageExecutor  # noqa: E402
from scan_supervisor import StepLease  # noqa: E402


class RecordingLoader:
    def __init__(self, materials):
        self.materials = list(materials)
        self.calls = []

    def load_materials(self, *, run_id):
        self.calls.append(run_id)
        return list(self.materials)


def _lease(stage_name="material_ingestion"):
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    return StepLease(
        run_id="RUN-001",
        stage_name=stage_name,
        attempt=1,
        lease_owner="test-owner",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=30),
    )


def test_adapter_prepares_a_checkpoint_without_a_state_mutation_method():
    materials = prepare_scan_materials(
        run_id="RUN-001",
        report_text="Submitted context",
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
    ).materials
    loader = RecordingLoader(materials)

    result = MaterialIngestionStageExecutor(loader).execute(_lease())

    assert loader.calls == ["RUN-001"]
    assert result.next_stage == "intent"
    assert result.checkpoint is not None
    assert result.checkpoint.gate_result["status"] == "passed"
    assert result.output_hash == result.checkpoint.output_hash
    assert result.checkpoint_ref == result.checkpoint.checkpoint_ref
    assert not hasattr(MaterialIngestionStageExecutor, "complete_step")


def test_adapter_returns_blocked_result_without_queuing_intent():
    failed_material = prepare_scan_materials(
        run_id="RUN-001",
        report_text="",
        uploads=[{"filename": "broken.pdf", "content": b"not a PDF"}],
    ).materials
    result = MaterialIngestionStageExecutor(RecordingLoader(failed_material)).execute(_lease())

    assert result.gate_result["status"] == "blocked"
    assert result.next_stage is None
    assert result.checkpoint is not None


def test_adapter_rejects_a_non_material_stage():
    loader = RecordingLoader([])

    with pytest.raises(ValueError, match="cannot execute"):
        MaterialIngestionStageExecutor(loader).execute(_lease(stage_name="intent"))
