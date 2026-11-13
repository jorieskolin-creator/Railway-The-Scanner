"""Disposable PostgreSQL contract for the prepare-only material adapter."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_stage_executor import MaterialIngestionStageExecutor  # noqa: E402
from scan_supervisor import ScanRepository  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)


@pytest.fixture(autouse=True)
def adapter_state_schema():
    migrations = [
        (_BACKEND / "migrations" / "001_scan_execution_state.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "002_scan_materials.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "003_scan_checkpoints.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "012_scan_runs_save_to_database.sql").read_text(encoding="utf-8"),
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS scan_checkpoints, scan_materials, scan_events, scan_steps, scan_runs CASCADE"
        )
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS scan_checkpoints, scan_materials, scan_events, scan_steps, scan_runs CASCADE"
        )


def test_adapter_reads_durable_material_but_does_not_commit_or_queue_next_stage():
    repository = ScanRepository(DATABASE_URL)
    ingress = prepare_scan_ingress(
        company_name="Adapter Contract Test",
        company_url="https://example.test",
        report_text="Submitted context",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
        idempotency_key="adapter-contract-001",
    )
    repository.create_run_with_materials(
        run_id=ingress.run_id,
        company_name=ingress.company_name,
        company_url=ingress.company_url,
        idempotency_key=ingress.idempotency_key,
        request_hash=ingress.request_hash,
        cognitive_contract_version=ingress.cognitive_contract_version,
        initial_stage=ingress.initial_stage,
        materials=list(ingress.materials),
    )
    lease = repository.claim_next_step(
        lease_owner="adapter-owner",
        lease_seconds=30,
        now=datetime.now(timezone.utc).replace(microsecond=0),
    )
    assert lease is not None

    result = MaterialIngestionStageExecutor(repository).execute(lease)

    assert result.checkpoint is not None
    assert result.next_stage == "intent"
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, current_stage
                FROM scan_runs
                WHERE run_id = %s
                """,
                (ingress.run_id,),
            )
            run = cursor.fetchone()
            cursor.execute(
                """
                SELECT status
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'material_ingestion' AND attempt = 1
                """,
                (ingress.run_id,),
            )
            step = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM scan_checkpoints WHERE run_id = %s",
                (ingress.run_id,),
            )
            checkpoint_count = cursor.fetchone()[0]

    assert run == ("running", "material_ingestion")
    assert step == ("leased",)
    assert checkpoint_count == 0
