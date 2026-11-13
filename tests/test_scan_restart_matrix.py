"""Hermetic contracts for Card 3J-restart full-chain restart and reclaim matrix."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_durable_loop import (  # noqa: E402
    DURABLE_STAGE_ORDER,
    commit_prepared_stage,
    drive_durable_chain,
    prepare_leased_stage,
    resume_durable_chain,
    verify_completed_stage_prefix,
)
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import (  # noqa: E402
    CheckpointIntegrityError,
    ScanRun,
    StaleLeaseError,
    StepLease,
)


class _MatrixMemoryRepository:
    """In-memory repository exercising the real stage executors, snapshots, and integrity checks."""

    def __init__(self, ingress):
        self.ingress = ingress
        self.run_id = ingress.run_id
        self.materials = list(ingress.materials)
        self.save_to_database = ingress.save_to_database
        self.run_status = "queued"
        self.current_stage: str | None = ingress.initial_stage
        self.steps = [
            {
                "stage_name": ingress.initial_stage,
                "attempt": 1,
                "status": "pending",
                "lease_owner": None,
                "fencing_token": 0,
                "lease_expires_at": None,
            }
        ]
        self.checkpoints = {}
        self.retrievals = {}
        self.jury_candidates = {}
        self.intent_snapshot = None
        self.jury_snapshot = None
        self.diagnosis_snapshot = None
        self.foresight_snapshot = None
        self.synthesis_snapshot = None
        self.verification_snapshot = None

    def load_run(self, *, run_id: str) -> ScanRun | None:
        if run_id != self.run_id:
            return None
        return ScanRun(
            run_id=self.run_id,
            company_name=self.ingress.company_name,
            company_url=self.ingress.company_url,
            cognitive_contract_version=self.ingress.cognitive_contract_version,
            status=self.run_status,
            current_stage=self.current_stage,
            idempotency_key=self.ingress.idempotency_key,
            request_hash=self.ingress.request_hash,
            save_to_database=self.save_to_database,
            final_artifact_ref=None,
        )

    def claim_next_step(self, *, lease_owner: str, lease_seconds: int, now: datetime):
        for step in self.steps:
            if step["status"] in ("leased", "running") and step["lease_expires_at"] > now:
                return None
        for step in self.steps:
            expired = (
                step["status"] in ("leased", "running")
                and step["lease_expires_at"] <= now
            )
            if step["status"] in ("pending", "retryable_failed") or expired:
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

    def load_materials(self, *, run_id: str):
        if run_id != self.run_id:
            raise KeyError(run_id)
        return list(self.materials)

    def persist_source_retrievals(self, *, retrievals):
        for retrieval in retrievals:
            key = (retrieval.run_id, retrieval.stage_name, retrieval.attempt)
            bucket = self.retrievals.setdefault(key, {})
            bucket[retrieval.retrieval_id] = retrieval

    def load_source_retrievals(self, *, run_id: str, stage_name: str, attempt: int):
        self._require_step(run_id, stage_name, attempt)
        bucket = self.retrievals.get((run_id, stage_name, attempt), {})
        return list(bucket.values())

    def persist_jury_source_candidates(self, *, candidates):
        for candidate in candidates:
            key = (candidate.run_id, candidate.stage_name, candidate.attempt)
            bucket = self.jury_candidates.setdefault(key, {})
            bucket[candidate.candidate_id] = candidate

    def load_jury_source_candidates(self, *, run_id: str, stage_name: str, attempt: int):
        self._require_step(run_id, stage_name, attempt)
        bucket = self.jury_candidates.get((run_id, stage_name, attempt), {})
        return list(bucket.values())

    def load_checkpoint(self, *, run_id: str, stage_name: str, attempt: int):
        return self.checkpoints.get((run_id, stage_name, attempt))

    def persist_intent_input_snapshot(self, *, snapshot):
        if self.intent_snapshot is not None and self.intent_snapshot != snapshot:
            raise ValueError("Intent input snapshot mismatch")
        self.intent_snapshot = snapshot

    def persist_jury_input_snapshot(self, *, snapshot):
        if self.jury_snapshot is not None and self.jury_snapshot != snapshot:
            raise ValueError("Jury input snapshot mismatch")
        self.jury_snapshot = snapshot

    def persist_diagnosis_input_snapshot(self, *, snapshot):
        if self.diagnosis_snapshot is not None and self.diagnosis_snapshot != snapshot:
            raise ValueError("Diagnosis input snapshot mismatch")
        self.diagnosis_snapshot = snapshot

    def persist_foresight_input_snapshot(self, *, snapshot):
        if self.foresight_snapshot is not None and self.foresight_snapshot != snapshot:
            raise ValueError("Foresight input snapshot mismatch")
        self.foresight_snapshot = snapshot

    def persist_synthesis_input_snapshot(self, *, snapshot):
        if self.synthesis_snapshot is not None and self.synthesis_snapshot != snapshot:
            raise ValueError("Synthesis input snapshot mismatch")
        self.synthesis_snapshot = snapshot

    def persist_verification_input_snapshot(self, *, snapshot):
        if self.verification_snapshot is not None and self.verification_snapshot != snapshot:
            raise ValueError("Verification input snapshot mismatch")
        self.verification_snapshot = snapshot

    def load_verified_intent_inputs(self, *, run_id: str, attempt: int):
        self._require_step(run_id, "intent", attempt)
        return (
            self.intent_snapshot,
            list(self.materials),
            self.load_source_retrievals(run_id=run_id, stage_name="intent", attempt=attempt),
        )

    def load_verified_jury_inputs(self, *, run_id: str, attempt: int):
        self._require_step(run_id, "jury_analysis", attempt)
        return (
            self.jury_snapshot,
            self.checkpoints[(run_id, "intent", 1)],
            self.load_jury_source_candidates(
                run_id=run_id, stage_name="jury_analysis", attempt=attempt
            ),
            self.load_source_retrievals(
                run_id=run_id, stage_name="jury_analysis", attempt=attempt
            ),
        )

    def load_verified_diagnosis_inputs(self, *, run_id: str, attempt: int):
        self._require_step(run_id, "diagnosis", attempt)
        return self.diagnosis_snapshot, self.checkpoints[(run_id, "jury_analysis", 1)]

    def load_verified_foresight_inputs(self, *, run_id: str, attempt: int):
        self._require_step(run_id, "foresight", attempt)
        return self.foresight_snapshot, self.checkpoints[(run_id, "diagnosis", 1)]

    def load_verified_synthesis_inputs(self, *, run_id: str, attempt: int):
        self._require_step(run_id, "strategic_synthesis", attempt)
        return self.synthesis_snapshot, self.checkpoints[(run_id, "foresight", 1)]

    def load_verified_verification_inputs(self, *, run_id: str, attempt: int):
        self._require_step(run_id, "verification", attempt)
        return self.verification_snapshot, self.checkpoints[(run_id, "strategic_synthesis", 1)]

    def complete_step_with_checkpoint(self, lease: StepLease, *, checkpoint, next_stage, now):
        step = self._require_step(lease.run_id, lease.stage_name, lease.attempt)
        if (
            step["status"] not in ("leased", "running")
            or step["lease_owner"] != lease.lease_owner
            or step["fencing_token"] != lease.fencing_token
            or step["lease_expires_at"] <= now
        ):
            raise StaleLeaseError("Lease lost before completing step.")
        self.checkpoints[(lease.run_id, lease.stage_name, lease.attempt)] = checkpoint
        step["status"] = "completed"
        if next_stage is None:
            self.run_status = "completed"
            self.current_stage = None
            return
        if not any(s["stage_name"] == next_stage and s["attempt"] == 1 for s in self.steps):
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

    def _require_step(self, run_id: str, stage_name: str, attempt: int):
        if run_id != self.run_id:
            raise KeyError(run_id)
        for step in self.steps:
            if step["stage_name"] == stage_name and step["attempt"] == attempt:
                return step
        raise KeyError(f"{stage_name}/{attempt}")


def _ingress(*, save_to_database: bool = False):
    return prepare_scan_ingress(
        company_name="Restart Matrix Oy",
        company_url="https://example.test",
        report_text="Customer autonomy, API modernization, and cloud engineering reliability.",
        save_to_database=save_to_database,
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
        idempotency_key="hermetic-restart-matrix-001",
    )


def _seed_stage_inputs(repository: _MatrixMemoryRepository, lease: StepLease) -> None:
    fixed_time = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    if lease.stage_name == "intent":
        repository.persist_source_retrievals(
            retrievals=[
                prepare_source_retrieval(
                    run_id=lease.run_id,
                    stage_name="intent",
                    attempt=lease.attempt,
                    retrieval_origin="intent_web_acquisition",
                    source_url="https://example.test/strategy",
                    retrieved_at=fixed_time,
                    http_status=200,
                    content_type="text/html",
                    response_content=b"<html><body>Platform strategy</body></html>",
                    extracted_text="Platform strategy and operating autonomy.",
                    parser_route="html/main-text/v1",
                    readability_status="readable",
                    retrieval_outcome="succeeded",
                )
            ]
        )
    elif lease.stage_name == "jury_analysis":
        repository.persist_jury_source_candidates(
            candidates=[
                prepare_jury_source_candidate(
                    run_id=lease.run_id,
                    attempt=lease.attempt,
                    candidate_origin="company_evidence_db",
                    source_url="https://example.test/engineering",
                    recorded_at=fixed_time,
                    parser_route="html/main-text/v1",
                    cached_extract_text="Engineering platform architecture notes.",
                    metadata={"discovery_label": "Engineering", "source_type": "official"},
                )
            ]
        )


@pytest.mark.parametrize("crash_stage", DURABLE_STAGE_ORDER)
def test_hermetic_restart_matrix_reclaims_every_stage_boundary_and_matches_clean_hashes(
    crash_stage: str,
):
    ingress = _ingress(save_to_database=False)
    clean_repo = _MatrixMemoryRepository(ingress)
    start = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    resume_durable_chain(
        clean_repo,
        lease_owner="clean-worker",
        now=start,
        lease_seconds=30,
        on_stage_leased=_seed_stage_inputs,
    )

    repo = _MatrixMemoryRepository(ingress)
    crash_idx = DURABLE_STAGE_ORDER.index(crash_stage)
    current_time = start
    if crash_idx > 0:
        prefix_result = resume_durable_chain(
            repo,
            lease_owner="worker-before-crash",
            now=current_time,
            lease_seconds=30,
            stop_after_stage=DURABLE_STAGE_ORDER[crash_idx - 1],
            on_stage_leased=_seed_stage_inputs,
        )
        assert prefix_result.completed_stages == DURABLE_STAGE_ORDER[:crash_idx]
        current_time = current_time + timedelta(seconds=crash_idx + 1)

    # First owner claims crash_stage, seeds inputs, prepares proposal, then loses its lease.
    stale_lease = repo.claim_next_step(
        lease_owner="worker-before-crash",
        lease_seconds=30,
        now=current_time,
    )
    assert stale_lease is not None
    assert stale_lease.stage_name == crash_stage
    _seed_stage_inputs(repo, stale_lease)
    stale_proposal = prepare_leased_stage(repo, stale_lease)

    # Contender cannot claim before lease expiry.
    assert (
        repo.claim_next_step(
            lease_owner="contender",
            lease_seconds=30,
            now=current_time + timedelta(seconds=10),
        )
        is None
    )

    # Lease expires; replacement worker reclaims and resumes through Verification.
    reclaim_time = current_time + timedelta(seconds=31)
    reclaimed_lease = repo.claim_next_step(
        lease_owner="worker-after-crash",
        lease_seconds=30,
        now=reclaim_time,
    )
    assert reclaimed_lease is not None
    assert reclaimed_lease.stage_name == crash_stage
    assert reclaimed_lease.fencing_token == stale_lease.fencing_token + 1

    # Stale worker's late commit fails closed.
    with pytest.raises(StaleLeaseError):
        commit_prepared_stage(repo, stale_lease, stale_proposal, now=reclaim_time)

    verify_completed_stage_prefix(
        repo,
        run_id=repo.run_id,
        completed_stages=DURABLE_STAGE_ORDER[:crash_idx],
    )
    reclaimed_proposal = prepare_leased_stage(repo, reclaimed_lease)
    commit_prepared_stage(repo, reclaimed_lease, reclaimed_proposal, now=reclaim_time)

    if crash_stage != "verification":
        tail_result = resume_durable_chain(
            repo,
            lease_owner="worker-after-crash",
            now=reclaim_time + timedelta(seconds=1),
            lease_seconds=30,
            on_stage_leased=_seed_stage_inputs,
        )
        assert tail_result.resumed_from_stage == DURABLE_STAGE_ORDER[crash_idx + 1]
        assert tail_result.verified_upstream_stages == DURABLE_STAGE_ORDER[: crash_idx + 1]

    assert repo.run_status == "completed"
    assert repo.current_stage is None
    assert repo.load_run(run_id=repo.run_id).save_to_database is False
    assert repo.checkpoints == clean_repo.checkpoints


def test_tampered_or_out_of_order_upstream_checkpoint_blocks_resume():
    ingress = _ingress(save_to_database=True)
    repo = _MatrixMemoryRepository(ingress)
    now = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    resume_durable_chain(
        repo,
        lease_owner="worker-1",
        now=now,
        lease_seconds=30,
        stop_after_stage="diagnosis",
    )

    # Mutate diagnosis snapshot JSON without changing output_hash.
    diagnosis_cp = repo.checkpoints[(repo.run_id, "diagnosis", 1)]
    corrupted_snapshot = json.loads(json.dumps(diagnosis_cp.snapshot))
    corrupted_snapshot["diagnosis_result"]["reality_score"] = 88
    repo.checkpoints[(repo.run_id, "diagnosis", 1)] = replace(
        diagnosis_cp, snapshot=corrupted_snapshot
    )

    with pytest.raises(CheckpointIntegrityError):
        resume_durable_chain(
            repo,
            lease_owner="worker-2",
            now=now + timedelta(seconds=10),
            lease_seconds=30,
        )

    # Restore valid diagnosis checkpoint, then inject an unexpected downstream checkpoint.
    repo.checkpoints[(repo.run_id, "diagnosis", 1)] = diagnosis_cp
    full_repo = _MatrixMemoryRepository(ingress)
    drive_durable_chain(full_repo, lease_owner="ref-worker", now=now)
    repo.checkpoints[(repo.run_id, "verification", 1)] = full_repo.checkpoints[
        (repo.run_id, "verification", 1)
    ]

    with pytest.raises(CheckpointIntegrityError, match="Unexpected downstream checkpoint"):
        verify_completed_stage_prefix(
            repo,
            run_id=repo.run_id,
            completed_stages=DURABLE_STAGE_ORDER[:4],
        )


def test_restart_helpers_remain_isolated_from_live_scan_and_blob_paths():
    loop_source = (_BACKEND / "scan_durable_loop.py").read_text(encoding="utf-8")
    main_source = (_BACKEND / "main.py").read_text(encoding="utf-8")
    supervisor_source = (_BACKEND / "scan_supervisor.py").read_text(encoding="utf-8")

    assert "scan_durable_loop" not in main_source
    assert "scan_durable_loop" not in supervisor_source
    assert "import fastapi" not in loop_source.lower()
    assert "blobstorage" not in loop_source.lower()
    assert "vercel" not in loop_source.lower()
    assert '"/scan"' not in loop_source
