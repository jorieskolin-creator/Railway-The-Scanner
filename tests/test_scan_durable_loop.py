"""Hermetic contracts for the test-only durable stage loop."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_durable_loop import DURABLE_STAGE_ORDER, drive_durable_chain  # noqa: E402
from scan_materials import prepare_scan_materials  # noqa: E402
from scan_supervisor import StepLease  # noqa: E402


class MemoryRepository:
    """Just enough of ScanRepository for the real adapters and commit helpers."""

    def __init__(self, materials):
        self.materials = list(materials)
        self.run_id = "RUN-LOOP-001"
        self.steps = [
            {
                "stage_name": "material_ingestion",
                "attempt": 1,
                "status": "pending",
                "lease_owner": None,
                "fencing_token": 0,
                "lease_expires_at": None,
            }
        ]
        self.checkpoints = {}
        self.intent_snapshot = None
        self.jury_snapshot = None
        self.diagnosis_snapshot = None
        self.foresight_snapshot = None
        self.synthesis_snapshot = None
        self.verification_snapshot = None
        self.run_status = "queued"
        self.current_stage = "material_ingestion"

    def claim_next_step(self, *, lease_owner, lease_seconds, now):
        for step in self.steps:
            expired = step["status"] == "leased" and step["lease_expires_at"] <= now
            if step["status"] == "pending" or expired:
                step["status"] = "leased"
                step["lease_owner"] = lease_owner
                step["fencing_token"] += 1
                step["lease_expires_at"] = now + timedelta(seconds=lease_seconds)
                self.run_status = "running"
                self.current_stage = step["stage_name"]
                return StepLease(
                    run_id=self.run_id,
                    stage_name=step["stage_name"],
                    attempt=step["attempt"],
                    lease_owner=lease_owner,
                    fencing_token=step["fencing_token"],
                    lease_expires_at=step["lease_expires_at"],
                )
        return None

    def load_materials(self, *, run_id):
        if run_id != self.run_id:
            raise KeyError(run_id)
        return list(self.materials)

    def load_source_retrievals(self, *, run_id, stage_name, attempt):
        self._require_step(run_id, stage_name, attempt)
        return []

    def load_jury_source_candidates(self, *, run_id, stage_name, attempt):
        self._require_step(run_id, stage_name, attempt)
        return []

    def load_checkpoint(self, *, run_id, stage_name, attempt):
        return self.checkpoints.get((run_id, stage_name, attempt))

    def persist_intent_input_snapshot(self, *, snapshot):
        self.intent_snapshot = snapshot

    def persist_jury_input_snapshot(self, *, snapshot):
        self.jury_snapshot = snapshot

    def persist_diagnosis_input_snapshot(self, *, snapshot):
        self.diagnosis_snapshot = snapshot

    def persist_foresight_input_snapshot(self, *, snapshot):
        self.foresight_snapshot = snapshot

    def persist_synthesis_input_snapshot(self, *, snapshot):
        self.synthesis_snapshot = snapshot

    def persist_verification_input_snapshot(self, *, snapshot):
        self.verification_snapshot = snapshot

    def load_verified_intent_inputs(self, *, run_id, attempt):
        self._require_step(run_id, "intent", attempt)
        return self.intent_snapshot, list(self.materials), []

    def load_verified_jury_inputs(self, *, run_id, attempt):
        self._require_step(run_id, "jury_analysis", attempt)
        return (
            self.jury_snapshot,
            self.checkpoints[(run_id, "intent", 1)],
            [],
            [],
        )

    def load_verified_diagnosis_inputs(self, *, run_id, attempt):
        self._require_step(run_id, "diagnosis", attempt)
        return self.diagnosis_snapshot, self.checkpoints[(run_id, "jury_analysis", 1)]

    def load_verified_foresight_inputs(self, *, run_id, attempt):
        self._require_step(run_id, "foresight", attempt)
        return self.foresight_snapshot, self.checkpoints[(run_id, "diagnosis", 1)]

    def load_verified_synthesis_inputs(self, *, run_id, attempt):
        self._require_step(run_id, "strategic_synthesis", attempt)
        return self.synthesis_snapshot, self.checkpoints[(run_id, "foresight", 1)]

    def load_verified_verification_inputs(self, *, run_id, attempt):
        self._require_step(run_id, "verification", attempt)
        return self.verification_snapshot, self.checkpoints[(run_id, "strategic_synthesis", 1)]

    def complete_step_with_checkpoint(self, lease, *, checkpoint, next_stage, now):
        del now
        step = self._require_step(lease.run_id, lease.stage_name, lease.attempt)
        if step["lease_owner"] != lease.lease_owner or step["fencing_token"] != lease.fencing_token:
            raise RuntimeError("stale lease")
        if step["lease_expires_at"] < datetime(2000, 1, 1, tzinfo=timezone.utc):
            raise RuntimeError("expired lease")
        self.checkpoints[(lease.run_id, lease.stage_name, lease.attempt)] = checkpoint
        step["status"] = "completed"
        if next_stage is None:
            self.run_status = "completed"
            self.current_stage = None
            return
        self.steps.append(
            {
                "stage_name": next_stage,
                "attempt": 1,
                "status": "pending",
                "lease_owner": None,
                "fencing_token": 0,
                "lease_expires_at": None,
            }
        )
        self.current_stage = next_stage

    def _require_step(self, run_id, stage_name, attempt):
        if run_id != self.run_id:
            raise KeyError(run_id)
        for step in self.steps:
            if step["stage_name"] == stage_name and step["attempt"] == attempt:
                return step
        raise KeyError(f"{stage_name}/{attempt}")


def _materials():
    return prepare_scan_materials(
        run_id="RUN-LOOP-001",
        report_text="Submitted company context for the durable loop.",
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
    ).materials


def test_driver_walks_every_stage_and_stops_after_verification():
    repository = MemoryRepository(_materials())

    result = drive_durable_chain(
        repository,
        lease_owner="loop-owner",
        now=datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc),
    )

    assert result.stages == DURABLE_STAGE_ORDER
    assert result.reclaimed_stage is None
    assert repository.run_status == "completed"
    assert repository.current_stage is None
    assert [step["stage_name"] for step in repository.steps] == list(DURABLE_STAGE_ORDER)
    assert all(step["status"] == "completed" for step in repository.steps)
    assert set(repository.checkpoints) == {
        ("RUN-LOOP-001", stage, 1) for stage in DURABLE_STAGE_ORDER
    }
    verification = repository.checkpoints[("RUN-LOOP-001", "verification", 1)]
    assert verification.gate_result["status"] == "passed"


def test_driver_reclaims_one_expired_stage_under_a_new_owner():
    repository = MemoryRepository(_materials())

    result = drive_durable_chain(
        repository,
        lease_owner="first-owner",
        now=datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc),
        lease_seconds=30,
        reclaim_stage="diagnosis",
        replacement_owner="second-owner",
    )

    diagnosis = next(step for step in repository.steps if step["stage_name"] == "diagnosis")
    assert result.reclaimed_stage == "diagnosis"
    assert result.reclaimed_owner == "second-owner"
    assert diagnosis["lease_owner"] == "second-owner"
    assert diagnosis["fencing_token"] == 2
    assert diagnosis["status"] == "completed"
    assert repository.run_status == "completed"


def test_runner_is_not_registered_on_the_live_path():
    loop_source = Path(_BACKEND, "scan_durable_loop.py").read_text(encoding="utf-8")
    main_source = Path(_BACKEND, "main.py").read_text(encoding="utf-8")
    supervisor_source = Path(_BACKEND, "scan_supervisor.py").read_text(encoding="utf-8")

    assert "import fastapi" not in loop_source.lower()
    assert "scan_durable_loop" not in main_source
    assert "scan_durable_loop" not in supervisor_source
    assert "vercel" not in loop_source.lower()
    assert "blob_store" not in loop_source.lower()
    assert '"/scan"' not in loop_source
    with pytest.raises(ValueError, match="not in the durable chain"):
        drive_durable_chain(
            MemoryRepository(_materials()),
            lease_owner="loop-owner",
            now=datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc),
            reclaim_stage="publication",
            replacement_owner="other",
        )
