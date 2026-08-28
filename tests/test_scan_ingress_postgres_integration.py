"""Disposable PostgreSQL contract for transactional Scanner ingress."""

from __future__ import annotations

from dataclasses import replace
import os
import sys
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_supervisor import ScanRepository  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)


@pytest.fixture(autouse=True)
def ingress_state_schema():
    migrations = [
        (_BACKEND / "migrations" / "001_scan_execution_state.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "002_scan_materials.sql").read_text(encoding="utf-8"),
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS scan_materials, scan_events, scan_steps, scan_runs CASCADE"
        )
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS scan_materials, scan_events, scan_steps, scan_runs CASCADE"
        )


def _create(repository: ScanRepository, ingress):
    return repository.create_run_with_materials(
        run_id=ingress.run_id,
        company_name=ingress.company_name,
        company_url=ingress.company_url,
        idempotency_key=ingress.idempotency_key,
        request_hash=ingress.request_hash,
        cognitive_contract_version=ingress.cognitive_contract_version,
        initial_stage=ingress.initial_stage,
        materials=list(ingress.materials),
    )


def test_transactional_ingress_creates_one_run_and_immutable_materials_once():
    repository = ScanRepository(DATABASE_URL)
    ingress = prepare_scan_ingress(
        company_name="Ingress Contract Test",
        company_url="https://example.test",
        report_text="Submitted context",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
        idempotency_key="ingress-contract-001",
    )

    created = _create(repository, ingress)
    replayed = _create(repository, ingress)

    assert created["run_id"] == ingress.run_id
    assert replayed == created
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, current_stage FROM scan_runs WHERE run_id = %s",
                (ingress.run_id,),
            )
            run = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM scan_steps WHERE run_id = %s",
                (ingress.run_id,),
            )
            step_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM scan_materials WHERE run_id = %s",
                (ingress.run_id,),
            )
            material_count = cursor.fetchone()[0]

    assert run == ("queued", "material_ingestion")
    assert step_count == 1
    assert material_count == 2


def test_same_idempotency_key_rejects_changed_input_without_creating_more_rows():
    repository = ScanRepository(DATABASE_URL)
    first = prepare_scan_ingress(
        company_name="Ingress Contract Test",
        company_url="https://example.test",
        report_text="first submission",
        save_to_database=True,
        uploads=[],
        idempotency_key="ingress-contract-001",
    )
    changed = prepare_scan_ingress(
        company_name="Ingress Contract Test",
        company_url="https://example.test",
        report_text="changed submission",
        save_to_database=True,
        uploads=[],
        idempotency_key="ingress-contract-001",
    )
    _create(repository, first)

    with pytest.raises(ValueError, match="different scan input"):
        _create(repository, changed)

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM scan_runs")
            run_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM scan_materials")
            material_count = cursor.fetchone()[0]

    assert (run_count, material_count) == (1, 1)


def test_failed_material_validation_rolls_back_the_new_run_and_step():
    repository = ScanRepository(DATABASE_URL)
    ingress = prepare_scan_ingress(
        company_name="Rollback Contract Test",
        company_url="https://example.test",
        report_text="submitted context",
        save_to_database=True,
        uploads=[],
        idempotency_key="ingress-contract-invalid",
    )
    invalid_material = replace(ingress.materials[0], extraction_status="unknown")

    with pytest.raises(ValueError, match="extraction_status"):
        repository.create_run_with_materials(
            run_id=ingress.run_id,
            company_name=ingress.company_name,
            company_url=ingress.company_url,
            idempotency_key=ingress.idempotency_key,
            request_hash=ingress.request_hash,
            cognitive_contract_version=ingress.cognitive_contract_version,
            initial_stage=ingress.initial_stage,
            materials=[invalid_material],
        )

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM scan_runs WHERE run_id = %s", (ingress.run_id,))
            run_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM scan_steps WHERE run_id = %s", (ingress.run_id,))
            step_count = cursor.fetchone()[0]

    assert (run_count, step_count) == (0, 0)
