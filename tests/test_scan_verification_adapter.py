"""Phase 3 contracts for the prepare-only Verification adapter."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_supervisor import ScanCheckpoint, StepLease  # noqa: E402
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
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402


class RecordingLoader:
    def __init__(self, snapshot, synthesis_checkpoint):
        self.snapshot = snapshot
        self.synthesis_checkpoint = synthesis_checkpoint
        self.calls = []

    def load_verified_verification_inputs(self, *, run_id, attempt):
        self.calls.append((run_id, attempt))
        return self.snapshot, self.synthesis_checkpoint


def _synthesis_checkpoint(*, run_id="RUN-VER-001"):
    return ScanCheckpoint(
        checkpoint_id="CP-" + "S" * 32,
        run_id=run_id,
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


def _snapshot(checkpoint=None):
    checkpoint = _synthesis_checkpoint() if checkpoint is None else checkpoint
    return build_verification_input_snapshot(
        run_id="RUN-VER-001",
        attempt=1,
        synthesis_checkpoint=checkpoint,
    )


def _lease(stage_name="verification"):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    return StepLease(
        run_id="RUN-VER-001",
        stage_name=stage_name,
        attempt=1,
        lease_owner="test-owner",
        fencing_token=1,
        lease_expires_at=now + timedelta(seconds=30),
    )


def test_adapter_wraps_verified_synthesis_output_without_a_state_mutation_method():
    checkpoint = _synthesis_checkpoint()
    snapshot = _snapshot(checkpoint)
    loader = RecordingLoader(snapshot, checkpoint)
    projection = build_verification_input_projection(snapshot, synthesis_checkpoint=checkpoint)

    result = VerificationStageExecutor(loader).execute(_lease())

    assert loader.calls == [("RUN-VER-001", 1)]
    assert result.checkpoint is not None
    assert result.checkpoint == build_verification_checkpoint(
        projection, verification_result=analyze_verification_from_synthesis(projection)
    )
    assert result.next_stage is None
    assert result.gate_result["status"] == "passed"
    assert result.checkpoint.snapshot["verification_result"] == empty_verification_result()
    assert result.output_hash == result.checkpoint.output_hash
    assert result.checkpoint_ref == result.checkpoint.checkpoint_ref
    assert not hasattr(VerificationStageExecutor, "complete_step")
    assert not hasattr(VerificationStageExecutor, "claim_next_step")
    assert not hasattr(VerificationStageExecutor, "persist")


def test_adapter_rejects_a_non_verification_stage():
    checkpoint = _synthesis_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    with pytest.raises(ValueError, match="cannot execute"):
        VerificationStageExecutor(loader).execute(_lease(stage_name="strategic_synthesis"))
    assert loader.calls == []


def test_malformed_injected_result_cannot_name_a_later_stage():
    checkpoint = _synthesis_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    result = VerificationStageExecutor(
        loader,
        analyze_verification=lambda _projection: {"commercial_pitch": "Buy now."},
    ).execute(_lease())

    assert result.gate_result["status"] == "validation_failed"
    assert result.next_stage is None
    assert result.checkpoint is not None
    assert "verification_result" not in result.checkpoint.snapshot


def test_passed_proposal_does_not_name_a_later_stage():
    checkpoint = _synthesis_checkpoint()
    loader = RecordingLoader(_snapshot(checkpoint), checkpoint)

    result = VerificationStageExecutor(loader).execute(_lease())

    assert result.gate_result["status"] == "passed"
    assert result.next_stage is None


def test_adapter_does_not_import_acquisition_or_live_scan_paths():
    source = Path(_BACKEND, "scan_verification_adapter.py").read_text(encoding="utf-8")

    assert "generate_foresight" not in source
    assert "strategic_foresight" not in source
    assert "foresight_harvester" not in source
    assert "x_ray_search" not in source
    assert "scrape_strategic_content" not in source
    assert "scan_ingress" not in source
    assert "complete_step" not in source
    assert "commit_verification" not in source
    assert "from main" not in source
    assert "import main" not in source
    assert "brave" not in source.lower()
    assert "firecrawl" not in source.lower()
    assert "coresignal" not in source.lower()
    assert "kb_versions" not in source
    assert "POST /scan" not in source
    assert "company_summary" not in source
    assert "publication" not in source
    assert "final_index" not in source
