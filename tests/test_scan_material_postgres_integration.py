"""Disposable PostgreSQL contract for Scanner material durability."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_materials import prepare_scan_materials  # noqa: E402
from scan_supervisor import ScanRepository  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)


@pytest.fixture(autouse=True)
def material_state_schema():
    migrations = [
        (_BACKEND / "migrations" / "001_scan_execution_state.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "002_scan_materials.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "012_scan_runs_save_to_database.sql").read_text(encoding="utf-8"),
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


def test_materials_preserve_extracted_text_and_hashes_without_raw_bytes():
    repository = ScanRepository(DATABASE_URL)
    run_id = f"RUN-{uuid4()}"
    repository.create_run(
        run_id=run_id,
        company_name="Material Contract Test",
        company_url="https://example.test",
        idempotency_key=f"IDEMP-{uuid4()}",
        request_hash="a" * 64,
        cognitive_contract_version="scanner-cognitive/v1",
        initial_stage="material_ingestion",
    )
    prepared = prepare_scan_materials(
        run_id=run_id,
        report_text="Submitted operating context.",
        uploads=[
            {
                "filename": "brief.txt",
                "content": b"Customer-provided operating brief.",
                "content_type": "text/plain",
            }
        ],
    )

    persisted = repository.persist_materials(
        run_id=run_id,
        materials=list(prepared.materials),
    )

    assert len(persisted) == 2
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT origin, filename, media_type, byte_size, content_sha256,
                       extracted_text_sha256, parser_version, extraction_status,
                       extraction_warnings, allowed_use_classification, extracted_text
                FROM scan_materials
                WHERE run_id = %s
                ORDER BY created_at, material_id
                """,
                (run_id,),
            )
            rows = cursor.fetchall()

    assert len(rows) == 2
    upload = next(row for row in rows if row[0] == "uploaded_file")
    assert upload[1:4] == ("brief.txt", "text/plain", len(b"Customer-provided operating brief."))
    assert len(upload[4]) == 64
    assert len(upload[5]) == 64
    assert upload[6] == "scanner-material-extractor/v1"
    assert upload[7] == "extracted"
    assert upload[8] == {"warnings": []}
    assert upload[9] == "company_provided_context"
    assert upload[10] == "Customer-provided operating brief."
