"""Disposable PostgreSQL contracts for immutable material checkpoints."""

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

from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_supervisor import MaterialIntegrityError, ScanRepository, StaleLeaseError  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)


@pytest.fixture(autouse=True)
def checkpoint_state_schema():
    migrations = [
        (_BACKEND / "migrations" / "001_scan_execution_state.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "002_scan_materials.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "003_scan_checkpoints.sql").read_text(encoding="utf-8"),
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


def _ingress():
    return prepare_scan_ingress(
        company_name="Checkpoint Contract Test",
        company_url="https://example.test",
        report_text="Submitted context",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
        idempotency_key="checkpoint-contract-001",
    )


def _create(repository: ScanRepository, ingress):
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


def test_checkpoint_and_step_completion_are_atomic_and_queue_intent_next():
    repository = ScanRepository(DATABASE_URL)
    ingress = _ingress()
    _create(repository, ingress)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    lease = repository.claim_next_step(
        lease_owner="checkpoint-owner",
        lease_seconds=30,
        now=start,
    )
    assert lease is not None

    materials = repository.load_materials(run_id=ingress.run_id)
    checkpoint = build_material_ingestion_checkpoint(
        run_id=ingress.run_id,
        attempt=lease.attempt,
        materials=materials,
    )
    repository.complete_step_with_checkpoint(
        lease,
        checkpoint=checkpoint,
        next_stage="intent",
        now=start + timedelta(seconds=1),
    )

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, checkpoint_ref, output_hash
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'material_ingestion' AND attempt = 1
                """,
                (ingress.run_id,),
            )
            completed_step = cursor.fetchone()
            cursor.execute(
                """
                SELECT status FROM scan_steps
                WHERE run_id = %s AND stage_name = 'intent' AND attempt = 1
                """,
                (ingress.run_id,),
            )
            intent_step = cursor.fetchone()
            cursor.execute(
                """
                SELECT contract_version, input_hash, output_hash, gate_result, snapshot
                FROM scan_checkpoints
                WHERE checkpoint_id = %s
                """,
                (checkpoint.checkpoint_id,),
            )
            stored_checkpoint = cursor.fetchone()

    assert completed_step == ("completed", checkpoint.checkpoint_ref, checkpoint.output_hash)
    assert intent_step == ("pending",)
    assert stored_checkpoint[:3] == (
        "scanner.material-ingestion/v1",
        checkpoint.input_hash,
        checkpoint.output_hash,
    )
    assert stored_checkpoint[3] == checkpoint.gate_result
    assert stored_checkpoint[4] == checkpoint.snapshot
    assert all(
        "extracted_text" not in reference
        for reference in stored_checkpoint[4]["material_refs"]
    )


def test_stale_checkpoint_attempt_leaves_no_checkpoint_row():
    repository = ScanRepository(DATABASE_URL)
    ingress = _ingress()
    _create(repository, ingress)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    first = repository.claim_next_step(
        lease_owner="owner-before-restart",
        lease_seconds=30,
        now=start,
    )
    assert first is not None
    reclaimed = repository.claim_next_step(
        lease_owner="owner-after-restart",
        lease_seconds=30,
        now=start + timedelta(seconds=31),
    )
    assert reclaimed is not None
    checkpoint = build_material_ingestion_checkpoint(
        run_id=ingress.run_id,
        attempt=first.attempt,
        materials=repository.load_materials(run_id=ingress.run_id),
    )

    with pytest.raises(StaleLeaseError):
        repository.complete_step_with_checkpoint(
            first,
            checkpoint=checkpoint,
            next_stage="intent",
            now=start + timedelta(seconds=32),
        )

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM scan_checkpoints WHERE run_id = %s", (ingress.run_id,))
            checkpoint_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT lease_owner, fencing_token
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'material_ingestion' AND attempt = 1
                """,
                (ingress.run_id,),
            )
            active_lease = cursor.fetchone()

    assert checkpoint_count == 0
    assert active_lease == (reclaimed.lease_owner, reclaimed.fencing_token)


def test_load_materials_rejects_a_tampered_extracted_text_hash():
    repository = ScanRepository(DATABASE_URL)
    ingress = _ingress()
    _create(repository, ingress)

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_materials
            SET extracted_text = 'tampered after persistence'
            WHERE run_id = %s AND origin = 'uploaded_file'
            """,
            (ingress.run_id,),
        )

    with pytest.raises(MaterialIntegrityError, match="hash mismatch"):
        repository.load_materials(run_id=ingress.run_id)
