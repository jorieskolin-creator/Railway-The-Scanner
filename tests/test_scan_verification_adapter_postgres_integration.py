"""Disposable PostgreSQL contract for the prepare-only Verification adapter."""

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

from scan_supervisor import ScanRepository  # noqa: E402
from scan_verification_adapter import VerificationStageExecutor  # noqa: E402
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
def verification_adapter_schema():
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


def test_adapter_reads_verified_inputs_but_does_not_commit_or_queue_a_successor():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)
    ingress, synthesis_attempt = _queue_pending_verification(
        repository, now=now, lease_owner="verification-adapter-owner"
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
        lease_owner="verification-adapter-verification",
        lease_seconds=30,
        now=now + timedelta(seconds=12),
    )
    assert verification_lease is not None
    assert verification_lease.stage_name == "verification"

    result = VerificationStageExecutor(repository).execute(verification_lease)

    assert result.checkpoint is not None
    assert result.next_stage is None
    assert result.gate_result["status"] == "passed"
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, current_stage FROM scan_runs WHERE run_id = %s",
                (ingress.run_id,),
            )
            run = cursor.fetchone()
            cursor.execute(
                """
                SELECT status
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'verification' AND attempt = %s
                """,
                (ingress.run_id, verification_lease.attempt),
            )
            step = cursor.fetchone()
            cursor.execute(
                """
                SELECT count(*)
                FROM scan_checkpoints
                WHERE run_id = %s AND stage_name = 'verification'
                """,
                (ingress.run_id,),
            )
            verification_checkpoint_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*)
                FROM scan_steps
                WHERE run_id = %s
                  AND stage_name NOT IN (
                      'material_ingestion', 'intent', 'jury_analysis', 'diagnosis',
                      'foresight', 'strategic_synthesis', 'verification'
                  )
                """,
                (ingress.run_id,),
            )
            successor_step_count = cursor.fetchone()[0]

    assert run == ("running", "verification")
    assert step == ("leased",)
    assert verification_checkpoint_count == 0
    assert successor_step_count == 0
