"""Phase 3 contracts for the prepare-only Foresight adapter."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_diagnosis_checkpoint import DIAGNOSIS_CHECKPOINT_CONTRACT  # noqa: E402
from scan_foresight_adapter import (  # noqa: E402
    ForesightStageExecutor,
    analyze_foresight_from_diagnosis,
)
from scan_foresight_checkpoint import (  # noqa: E402
    build_foresight_checkpoint,
    build_foresight_input_projection,
    empty_foresight_result,
)
from scan_foresight_snapshot import build_foresight_input_snapshot  # noqa: E402
from scan_supervisor import ScanCheckpoint, StepLease  # noqa: E402


class RecordingLoader:
    def __init__(self, snapshot, diagnosis_checkpoint):
        self.snapshot = snapshot
        self.diagnosis_checkpoint = diagnosis_checkpoint
        self.calls = []

    def load_verified_foresight_inputs(self, *, run_id, attempt):
        self.calls.append((run_id, attempt))
        return self.snapshot, self.diagnosis_checkpoint


def _diagnosis_checkpoint(*, run_id="RUN-FOR-001"):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "D" * 32,
        run_id=run_id,
        stage_name="diagnosis",
        attempt=1,
        contract_version=DIAGNOSIS_CHECKPOINT_CONTRACT,
        input_hash="a" * 64,
        output_hash="b" * 64,
        gate_result={
            "gate": DIAGNOSIS_CHECKPOINT_CONTRACT,
            "status": "passed",
            "reasons": [],
            "warnings": [],
        },
        snapshot={
            "contract": DIAGNOSIS_CHECKPOINT_CONTRACT,
            "diagnosis_result": {
                "diagnostic_summary": "",
                "evidence_based_risks": [],
                "reality_score": 0,
                "archetype": "At Risk",
            },
        },
    )


def _snapshot(checkpoint=None):
    checkpoint = _diagnosis_checkpoint() if checkpoint is None else checkpoint
    return build_foresight_input_snapshot(
        run_id="RUN-FOR-001",
        attempt=1,
        diagnosis_checkpoint=checkpoint,
    )


def _lease(stage_name="foresight"):
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    return StepLease(
        run_id="RUN-FOR-001",
        stage_name=stage_name,
        attempt=1,
        lease_owner="test-owner",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=30),
    )


def test_adapter_wraps_verified_diagnosis_output_without_a_state_mutation_method():
    checkpoint = _diagnosis_checkpoint()
    snapshot = _snapshot(checkpoint)
    loader = RecordingLoader(snapshot, checkpoint)
    projection = build_foresight_input_projection(snapshot, diagnosis_checkpoint=checkpoint)

    result = ForesightStageExecutor(loader).execute(_lease())

    assert loader.calls == [("RUN-FOR-001", 1)]
    assert result.checkpoint is not None
    assert result.checkpoint == build_foresight_checkpoint(
        projection, foresight_result=analyze_foresight_from_diagnosis(projection)
    )
    assert result.next_stage == "strategic_synthesis"
    assert result.gate_result["status"] == "passed"
    assert result.checkpoint.snapshot["foresight_result"] == empty_foresight_result()
    assert result.output_hash == result.checkpoint.output_hash
    assert result.checkpoint_ref == result.checkpoint.checkpoint_ref
    assert not hasattr(ForesightStageExecutor, "complete_step")
    assert not hasattr(ForesightStageExecutor, "claim_next_step")
    assert not hasattr(ForesightStageExecutor, "persist")


def test_adapter_rejects_a_non_foresight_stage():
    checkpoint = _diagnosis_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    with pytest.raises(ValueError, match="cannot execute"):
        ForesightStageExecutor(loader).execute(_lease(stage_name="diagnosis"))
    assert loader.calls == []


def test_malformed_injected_result_cannot_queue_synthesis():
    checkpoint = _diagnosis_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    result = ForesightStageExecutor(
        loader,
        analyze_foresight=lambda _projection: {"dimension_forces": []},
    ).execute(_lease())

    assert result.gate_result["status"] == "validation_failed"
    assert result.next_stage is None
    assert result.checkpoint is not None
    assert "foresight_result" not in result.checkpoint.snapshot


def test_adapter_does_not_import_acquisition_or_live_scan_paths():
    source = Path(_BACKEND, "scan_foresight_adapter.py").read_text(encoding="utf-8")

    assert "generate_foresight" not in source
    assert "strategic_foresight" not in source
    assert "foresight_harvester" not in source
    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "scan_ingress" not in source
    assert "complete_step" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "kb_versions" not in source
    assert "POST /scan" not in source
