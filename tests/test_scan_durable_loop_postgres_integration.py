"""Disposable PostgreSQL contract for the test-only full durable chain."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_durable_loop import DURABLE_STAGE_ORDER, drive_durable_chain  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_supervisor import ScanRepository  # noqa: E402


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
def durable_loop_schema():
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


def _create_run(repository: ScanRepository):
    ingress = prepare_scan_ingress(
        company_name="Durable Loop Contract Test",
        company_url="https://example.test",
        report_text="Submitted company context for the durable loop.",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
        idempotency_key=f"durable-loop-{uuid4()}",
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
        save_to_database=ingress.save_to_database,
    )
    return ingress


def _chain_state(run_id: str):
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, current_stage FROM scan_runs WHERE run_id = %s",
                (run_id,),
            )
            run = cursor.fetchone()
            cursor.execute(
                """
                SELECT stage_name, status, lease_owner, fencing_token
                FROM scan_steps
                WHERE run_id = %s
                ORDER BY created_at, stage_name
                """,
                (run_id,),
            )
            steps = cursor.fetchall()
            cursor.execute(
                """
                SELECT stage_name, count(*)
                FROM scan_checkpoints
                WHERE run_id = %s
                GROUP BY stage_name
                ORDER BY stage_name
                """,
                (run_id,),
            )
            checkpoints = cursor.fetchall()
            cursor.execute(
                "SELECT to_regclass('public.artifacts'), to_regclass('public.scan_artifacts')"
            )
            artifact_tables = cursor.fetchone()
    return run, steps, checkpoints, artifact_tables


def test_full_chain_completes_after_one_lease_reclaim_without_publication():
    repository = ScanRepository(DATABASE_URL)
    ingress = _create_run(repository)

    result = drive_durable_chain(
        repository,
        lease_owner="loop-before-restart",
        now=datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc),
        lease_seconds=30,
        reclaim_stage="diagnosis",
        replacement_owner="loop-after-restart",
    )
    run, steps, checkpoints, artifact_tables = _chain_state(ingress.run_id)

    assert result.run_id == ingress.run_id
    assert result.stages == DURABLE_STAGE_ORDER
    assert result.reclaimed_stage == "diagnosis"
    assert result.reclaimed_owner == "loop-after-restart"
    assert run == ("completed", None)
    assert [step[0] for step in steps] == list(DURABLE_STAGE_ORDER)
    assert all(step[1] == "completed" for step in steps)
    diagnosis = next(step for step in steps if step[0] == "diagnosis")
    assert diagnosis[2] == "loop-after-restart"
    assert diagnosis[3] == 2
    assert dict(checkpoints) == {stage: 1 for stage in DURABLE_STAGE_ORDER}
    assert artifact_tables == (None, None)
