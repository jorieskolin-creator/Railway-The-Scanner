"""Disposable PostgreSQL contracts for durable source retrievals."""

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

from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import ScanRepository, SourceRetrievalIntegrityError  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)


@pytest.fixture(autouse=True)
def retrieval_state_schema():
    migrations = [
        (_BACKEND / "migrations" / "001_scan_execution_state.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "002_scan_materials.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "003_scan_checkpoints.sql").read_text(encoding="utf-8"),
        (_BACKEND / "migrations" / "004_source_retrievals.sql").read_text(encoding="utf-8"),
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS source_retrievals, scan_checkpoints, scan_materials, scan_events, scan_steps, scan_runs CASCADE"
        )
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS source_retrievals, scan_checkpoints, scan_materials, scan_events, scan_steps, scan_runs CASCADE"
        )


def _run(repository: ScanRepository):
    ingress = prepare_scan_ingress(
        company_name="Retrieval Contract Test",
        company_url="https://example.test",
        report_text="Submitted context",
        save_to_database=True,
        uploads=[],
        idempotency_key=f"retrieval-contract-{uuid4()}",
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
    return ingress


def _retrieval(run_id):
    from datetime import datetime, timezone

    return prepare_source_retrieval(
        run_id=run_id,
        stage_name="material_ingestion",
        attempt=1,
        retrieval_origin="intent_web_acquisition",
        source_url="https://example.test/strategy/",
        retrieved_at=datetime(2026, 8, 28, 6, 0, tzinfo=timezone.utc),
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Acquired page</body></html>",
        extracted_text="Acquired page",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
        response_metadata={"provider": "contract-test", "content_length": 39},
    )


def test_retrieval_round_trip_is_idempotent_and_retains_no_raw_response_bytes():
    repository = ScanRepository(DATABASE_URL)
    ingress = _run(repository)
    retrieval = _retrieval(ingress.run_id)

    first = repository.persist_source_retrievals(retrievals=[retrieval])
    second = repository.persist_source_retrievals(retrievals=[retrieval])
    loaded = repository.load_source_retrievals(
        run_id=ingress.run_id,
        stage_name="material_ingestion",
        attempt=1,
    )

    assert first == second
    assert loaded == [retrieval]
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*), extracted_text, response_sha256, extracted_text_sha256,
                       retrieval_outcome, readability_status, response_metadata
                FROM source_retrievals
                WHERE run_id = %s
                GROUP BY extracted_text, response_sha256, extracted_text_sha256,
                         retrieval_outcome, readability_status, response_metadata
                """,
                (ingress.run_id,),
            )
            stored = cursor.fetchone()

    assert stored[0] == 1
    assert stored[1] == "Acquired page"
    assert len(stored[2]) == 64
    assert len(stored[3]) == 64
    assert stored[4:6] == ("succeeded", "readable")
    assert stored[6] == {"provider": "contract-test", "content_length": 39}


def test_tampered_retrieval_text_is_rejected_on_load():
    repository = ScanRepository(DATABASE_URL)
    ingress = _run(repository)
    retrieval = _retrieval(ingress.run_id)
    repository.persist_source_retrievals(retrievals=[retrieval])

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE source_retrievals
            SET extracted_text = 'tampered retrieval'
            WHERE retrieval_id = %s
            """,
            (retrieval.retrieval_id,),
        )

    with pytest.raises(SourceRetrievalIntegrityError, match="hash mismatch"):
        repository.load_source_retrievals(
            run_id=ingress.run_id,
            stage_name="material_ingestion",
            attempt=1,
        )
