"""Phase 3 contracts for the prepare-only Strategic Synthesis adapter."""

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
from scan_supervisor import ScanCheckpoint, StepLease  # noqa: E402
from scan_synthesis_adapter import (  # noqa: E402
    SynthesisStageExecutor,
    analyze_synthesis_from_foresight,
)
from scan_synthesis_checkpoint import (  # noqa: E402
    build_synthesis_checkpoint,
    build_synthesis_input_projection,
    empty_synthesis_result,
)
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402


class RecordingLoader:
    def __init__(self, snapshot, foresight_checkpoint):
        self.snapshot = snapshot
        self.foresight_checkpoint = foresight_checkpoint
        self.calls = []

    def load_verified_synthesis_inputs(self, *, run_id, attempt):
        self.calls.append((run_id, attempt))
        return self.snapshot, self.foresight_checkpoint


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


def test_adapter_wraps_verified_foresight_output_without_a_state_mutation_method():
    checkpoint = _foresight_checkpoint()
    snapshot = _snapshot(checkpoint)
    loader = RecordingLoader(snapshot, checkpoint)
    projection = build_synthesis_input_projection(snapshot, foresight_checkpoint=checkpoint)

    result = SynthesisStageExecutor(loader).execute(_lease())

    assert loader.calls == [("RUN-SYN-001", 1)]
    assert result.checkpoint is not None
    assert result.checkpoint == build_synthesis_checkpoint(
        projection, synthesis_result=analyze_synthesis_from_foresight(projection)
    )
    assert result.next_stage == "verification"
    assert result.gate_result["status"] == "passed"
    assert result.checkpoint.snapshot["synthesis_result"] == empty_synthesis_result()
    assert result.output_hash == result.checkpoint.output_hash
    assert result.checkpoint_ref == result.checkpoint.checkpoint_ref
    assert not hasattr(SynthesisStageExecutor, "complete_step")
    assert not hasattr(SynthesisStageExecutor, "claim_next_step")
    assert not hasattr(SynthesisStageExecutor, "persist")


def test_adapter_rejects_a_non_synthesis_stage():
    checkpoint = _foresight_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    with pytest.raises(ValueError, match="cannot execute"):
        SynthesisStageExecutor(loader).execute(_lease(stage_name="foresight"))
    assert loader.calls == []


def test_malformed_injected_result_cannot_queue_verification():
    checkpoint = _foresight_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    result = SynthesisStageExecutor(
        loader,
        analyze_synthesis=lambda _projection: {"commercial_pitch": "Buy now."},
    ).execute(_lease())

    assert result.gate_result["status"] == "validation_failed"
    assert result.next_stage is None
    assert result.checkpoint is not None
    assert "synthesis_result" not in result.checkpoint.snapshot


def test_adapter_does_not_import_acquisition_or_live_scan_paths():
    source = Path(_BACKEND, "scan_synthesis_adapter.py").read_text(encoding="utf-8")

    assert "generate_foresight" not in source
    assert "strategic_foresight" not in source
    assert "foresight_harvester" not in source
    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "scan_ingress" not in source
    assert "complete_step" not in source
    assert "commit_synthesis" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "kb_versions" not in source
    assert "POST /scan" not in source
