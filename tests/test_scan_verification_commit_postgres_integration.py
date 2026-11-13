"""Disposable PostgreSQL contracts for Verification fenced commit and reclaim."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_supervisor import (  # noqa: E402
    ScanRepository,
    StageExecutionResult,
    StaleLeaseError,
    VerificationInputSnapshotIntegrityError,
)
from scan_verification_adapter import (  # noqa: E402
    VerificationStageExecutor,
    analyze_verification_from_synthesis,
)
from scan_verification_checkpoint import (  # noqa: E402
    build_verification_checkpoint,
    build_verification_input_projection,
)
from scan_verification_commit import commit_verification_stage  # noqa: E402
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402
from test_scan_verification_snapshot_postgres_integration import (  # noqa: E402
    _queue_pending_verification,
)


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "verification_input_snapshots, synthesis_input_snapshots, foresight_input_snapshots, "
    "diagnosis_input_snapshots, jury_input_snapshots, jury_source_candidates, "
    "intent_input_snapshots, source_retrievals, scan_checkpoints, scan_materials, "
    "scan_events, scan_steps, scan_runs"
)


@pytest.fixture(autouse=True)
def verification_commit_schema():
    migrations = [
        (_BACKEND / "migrations" / name).read_text(encoding="utf-8")
        for name in (
            "001_scan_execution_state.sql",
            "002_scan_materials.sql",
            "003_scan_checkpoints.sql",
            "004_source_retrievals.sql",
            "005_intent_input_snapshots.sql",
            "006_jury_source_candidates.sql",
            "007_jury_input_snapshots.sql",
            "008_diagnosis_input_snapshots.sql",
            "009_foresight_input_snapshots.sql",
            "010_synthesis_input_snapshots.sql",
            "011_verification_input_snapshots.sql",
            "012_scan_runs_save_to_database.sql",
        )
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")


def _prepare_leased_verification(repository: ScanRepository, *, now: datetime, lease_owner: str):
    ingress, synthesis_attempt = _queue_pending_verification(
        repository, now=now, lease_owner=lease_owner
    )
    synthesis_checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="strategic_synthesis", attempt=synthesis_attempt
    )
    assert synthesis_checkpoint is not None
    snapshot = build_verification_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        synthesis_checkpoint=synthesis_checkpoint,
    )
    repository.persist_verification_input_snapshot(snapshot=snapshot)
    verification_lease = repository.claim_next_step(
        lease_owner=f"{lease_owner}-verification",
        lease_seconds=30,
        now=now + timedelta(seconds=12),
    )
    assert verification_lease is not None
    assert verification_lease.stage_name == "verification"
    return ingress, verification_lease, synthesis_attempt


def _run_state(run_id: str):
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, current_stage FROM scan_runs WHERE run_id = %s",
                (run_id,),
            )
            run = cursor.fetchone()
            cursor.execute(
                """
                SELECT status, checkpoint_ref, output_hash
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'verification' AND attempt = 1
                """,
                (run_id,),
            )
            verification_step = cursor.fetchone()
            cursor.execute(
                """
                SELECT count(*) FROM scan_checkpoints
                WHERE run_id = %s AND stage_name = 'verification'
                """,
                (run_id,),
            )
            verification_checkpoints = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*) FROM scan_steps
                WHERE run_id = %s
                  AND stage_name NOT IN (
                      'material_ingestion', 'intent', 'jury_analysis', 'diagnosis',
                      'foresight', 'strategic_synthesis', 'verification'
                  )
                """,
                (run_id,),
            )
            successor_steps = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*) FROM scan_events
                WHERE run_id = %s AND stage_name = 'verification'
                  AND event_type = 'step_completed'
                """,
                (run_id,),
            )
            completed_events = cursor.fetchone()[0]
    return run, verification_step, verification_checkpoints, successor_steps, completed_events


def test_passed_verification_commit_completes_the_run_without_a_successor():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    ingress, verification_lease, _synthesis_attempt = _prepare_leased_verification(
        repository, now=now, lease_owner="verification-commit-owner"
    )
    result = VerificationStageExecutor(repository).execute(verification_lease)

    committed = commit_verification_stage(
        repository, verification_lease, result, now=now + timedelta(seconds=13)
    )
    stored = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="verification", attempt=verification_lease.attempt
    )
    run, verification_step, verification_checkpoints, successor_steps, completed_events = _run_state(
        ingress.run_id
    )

    assert committed == result.checkpoint
    assert stored is not None
    assert stored.input_hash == committed.input_hash
    assert stored.output_hash == committed.output_hash
    assert stored.gate_result["status"] == "passed"
    assert run == ("completed", None)
    assert verification_step == ("completed", committed.checkpoint_ref, committed.output_hash)
    assert verification_checkpoints == 1
    assert successor_steps == 0
    assert completed_events == 1
    snapshot_text = str(stored.snapshot)
    assert "extracted_text" not in snapshot_text
    assert "synthesis_result" not in snapshot_text
    assert "foresight_result" not in snapshot_text
    assert stored.snapshot["verification_result"] == {"verification_record": ""}


def test_stale_verification_owner_cannot_commit_and_reclaiming_owner_can():
    repository = ScanRepository(DATABASE_URL)
    start = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    ingress, first, _synthesis_attempt = _prepare_leased_verification(
        repository, now=start, lease_owner="verification-before-restart"
    )
    first_result = VerificationStageExecutor(repository).execute(first)
    reclaimed = repository.claim_next_step(
        lease_owner="verification-after-restart",
        lease_seconds=30,
        now=start + timedelta(seconds=43),
    )
    assert reclaimed is not None
    assert reclaimed.stage_name == "verification"
    assert reclaimed.fencing_token == first.fencing_token + 1

    with pytest.raises(StaleLeaseError):
        commit_verification_stage(
            repository, first, first_result, now=start + timedelta(seconds=44)
        )

    run, verification_step, verification_checkpoints, successor_steps, _ = _run_state(ingress.run_id)
    assert run == ("running", "verification")
    assert verification_step[0] == "leased"
    assert verification_checkpoints == 0
    assert successor_steps == 0

    reclaimed_result = VerificationStageExecutor(repository).execute(reclaimed)
    committed = commit_verification_stage(
        repository, reclaimed, reclaimed_result, now=start + timedelta(seconds=45)
    )
    run, verification_step, verification_checkpoints, successor_steps, _ = _run_state(ingress.run_id)

    assert committed.checkpoint_id == first_result.checkpoint.checkpoint_id
    assert run == ("completed", None)
    assert verification_step[0] == "completed"
    assert verification_checkpoints == 1
    assert successor_steps == 0


def test_replay_of_the_same_passed_checkpoint_is_idempotent():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    ingress, verification_lease, _synthesis_attempt = _prepare_leased_verification(
        repository, now=now, lease_owner="verification-replay-owner"
    )
    result = VerificationStageExecutor(repository).execute(verification_lease)
    first = commit_verification_stage(
        repository, verification_lease, result, now=now + timedelta(seconds=13)
    )
    second = commit_verification_stage(
        repository, verification_lease, result, now=now + timedelta(seconds=14)
    )
    run, verification_step, verification_checkpoints, successor_steps, completed_events = _run_state(
        ingress.run_id
    )

    assert first == second
    assert run == ("completed", None)
    assert verification_step[0] == "completed"
    assert verification_checkpoints == 1
    assert successor_steps == 0
    assert completed_events == 1


def test_malformed_verification_proposal_cannot_complete_the_step():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    ingress, verification_lease, _synthesis_attempt = _prepare_leased_verification(
        repository, now=now, lease_owner="verification-invalid-owner"
    )
    snapshot, synthesis_checkpoint = repository.load_verified_verification_inputs(
        run_id=ingress.run_id,
        attempt=verification_lease.attempt,
    )
    projection = build_verification_input_projection(
        snapshot, synthesis_checkpoint=synthesis_checkpoint
    )
    failed = build_verification_checkpoint(
        projection, verification_result={"publication_status": "publish"}
    )

    with pytest.raises(ValueError, match="passed Verification gate"):
        commit_verification_stage(
            repository,
            verification_lease,
            StageExecutionResult(
                output_hash=failed.output_hash,
                checkpoint_ref=failed.checkpoint_ref,
                gate_result=failed.gate_result,
                next_stage=None,
                checkpoint=failed,
            ),
            now=now + timedelta(seconds=13),
        )
    run, verification_step, verification_checkpoints, successor_steps, completed_events = _run_state(
        ingress.run_id
    )
    assert run == ("running", "verification")
    assert verification_step[0] == "leased"
    assert verification_checkpoints == 0
    assert successor_steps == 0
    assert completed_events == 0


def test_restart_reload_keeps_verification_hashes_and_does_not_skip_the_gate():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    ingress, verification_lease, synthesis_attempt = _prepare_leased_verification(
        repository, now=now, lease_owner="verification-restart-owner"
    )
    result = VerificationStageExecutor(repository).execute(verification_lease)
    commit_verification_stage(
        repository, verification_lease, result, now=now + timedelta(seconds=13)
    )

    snapshot, synthesis_checkpoint = repository.load_verified_verification_inputs(
        run_id=ingress.run_id,
        attempt=verification_lease.attempt,
    )
    projection = build_verification_input_projection(
        snapshot, synthesis_checkpoint=synthesis_checkpoint
    )
    rebuilt = build_verification_checkpoint(
        projection, verification_result=analyze_verification_from_synthesis(projection)
    )
    stored = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="verification", attempt=verification_lease.attempt
    )
    run, verification_step, _, successor_steps, _ = _run_state(ingress.run_id)

    assert stored is not None
    assert rebuilt.input_hash == stored.input_hash == result.checkpoint.input_hash
    assert rebuilt.output_hash == stored.output_hash == result.checkpoint.output_hash
    assert stored.gate_result["status"] == "passed"
    assert verification_step[0] == "completed"
    assert successor_steps == 0
    assert run == ("completed", None)
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET output_hash = %s
            WHERE run_id = %s AND stage_name = 'strategic_synthesis' AND attempt = %s
            """,
            ("e" * 64, ingress.run_id, synthesis_attempt),
        )

    with pytest.raises(VerificationInputSnapshotIntegrityError):
        repository.load_verified_verification_inputs(
            run_id=ingress.run_id,
            attempt=verification_lease.attempt,
        )
