"""Disposable PostgreSQL contract for Scan Supervisor lease fencing."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_supervisor import ScanRepository, StaleLeaseError  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)


@pytest.fixture(autouse=True)
def execution_state_schema():
    """Apply the exact migration to an empty disposable database."""
    migrations = [
        (_BACKEND / "migrations" / "001_scan_execution_state.sql").read_text(
            encoding="utf-8"
        ),
        (_BACKEND / "migrations" / "012_scan_runs_save_to_database.sql").read_text(
            encoding="utf-8"
        ),
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS scan_events, scan_steps, scan_runs CASCADE"
        )
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS scan_events, scan_steps, scan_runs CASCADE"
        )


def test_expired_lease_is_safely_reclaimed_and_stale_attempt_cannot_commit():
    repository = ScanRepository(DATABASE_URL)
    run_id = f"RUN-{uuid4()}"
    start = datetime.now(timezone.utc).replace(microsecond=0)

    repository.create_run(
        run_id=run_id,
        company_name="Lease Contract Test",
        company_url="https://example.test",
        idempotency_key=f"IDEMP-{uuid4()}",
        request_hash="a" * 64,
        cognitive_contract_version="scanner-cognitive/v1",
        initial_stage="material_ingestion",
    )

    first = repository.claim_next_step(
        lease_owner="supervisor-before-restart",
        lease_seconds=30,
        now=start,
    )
    assert first is not None
    assert first.fencing_token == 1

    # A live lease keeps the single active run exclusive.
    assert repository.claim_next_step(
        lease_owner="supervisor-contender",
        lease_seconds=30,
        now=start + timedelta(seconds=10),
    ) is None

    reclaimed = repository.claim_next_step(
        lease_owner="supervisor-after-restart",
        lease_seconds=30,
        now=start + timedelta(seconds=31),
    )
    assert reclaimed is not None
    assert reclaimed.run_id == first.run_id
    assert reclaimed.stage_name == first.stage_name
    assert reclaimed.attempt == first.attempt
    assert reclaimed.fencing_token == first.fencing_token + 1

    # The lost process may finish work, but its old owner/token cannot publish it.
    with pytest.raises(StaleLeaseError):
        repository.complete_step(
            first,
            output_hash="b" * 64,
            checkpoint_ref="checkpoint://stale",
            gate_result={"gate": "material/v1", "status": "passed"},
            next_stage=None,
            now=start + timedelta(seconds=32),
        )

    repository.complete_step(
        reclaimed,
        output_hash="c" * 64,
        checkpoint_ref="checkpoint://current",
        gate_result={"gate": "material/v1", "status": "passed"},
        next_stage=None,
        now=start + timedelta(seconds=32),
    )

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, current_stage FROM scan_runs WHERE run_id = %s",
                (run_id,),
            )
            run_status, current_stage = cursor.fetchone()
            cursor.execute(
                """
                SELECT status, fencing_token, checkpoint_ref
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'material_ingestion' AND attempt = 1
                """,
                (run_id,),
            )
            step_status, fencing_token, checkpoint_ref = cursor.fetchone()
            cursor.execute(
                "SELECT event_type FROM scan_events WHERE run_id = %s ORDER BY event_id",
                (run_id,),
            )
            events = [row[0] for row in cursor.fetchall()]

    assert (run_status, current_stage) == ("completed", None)
    assert (step_status, fencing_token, checkpoint_ref) == (
        "completed",
        2,
        "checkpoint://current",
    )
    assert events == ["run_queued", "step_leased", "step_leased", "step_completed"]
