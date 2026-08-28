"""Disposable PostgreSQL contracts for Jury-stage candidate durability."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_intent_adapter import IntentStageExecutor  # noqa: E402
from scan_intent_commit import commit_intent_stage  # noqa: E402
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import (  # noqa: E402
    JurySourceCandidateIntegrityError,
    ScanRepository,
)


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "jury_source_candidates, intent_input_snapshots, source_retrievals, "
    "scan_checkpoints, scan_materials, scan_events, scan_steps, scan_runs"
)


@pytest.fixture(autouse=True)
def jury_candidate_schema():
    migrations = [
        (_BACKEND / "migrations" / name).read_text(encoding="utf-8")
        for name in (
            "001_scan_execution_state.sql",
            "002_scan_materials.sql",
            "003_scan_checkpoints.sql",
            "004_source_retrievals.sql",
            "005_intent_input_snapshots.sql",
            "006_jury_source_candidates.sql",
        )
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")


def _queue_pending_jury(repository: ScanRepository, *, now: datetime, lease_owner: str):
    ingress = prepare_scan_ingress(
        company_name="Jury Candidate Contract Test",
        company_url="https://example.test",
        report_text="Submitted context about customer intimacy and autonomy.",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
        idempotency_key=f"jury-candidate-{uuid4()}",
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
    material_lease = repository.claim_next_step(
        lease_owner=f"{lease_owner}-material",
        lease_seconds=30,
        now=now,
    )
    assert material_lease is not None
    repository.complete_step_with_checkpoint(
        material_lease,
        checkpoint=build_material_ingestion_checkpoint(
            run_id=ingress.run_id,
            attempt=material_lease.attempt,
            materials=repository.load_materials(run_id=ingress.run_id),
        ),
        next_stage="intent",
        now=now + timedelta(seconds=1),
    )
    intent_lease = repository.claim_next_step(
        lease_owner=lease_owner,
        lease_seconds=30,
        now=now + timedelta(seconds=2),
    )
    assert intent_lease is not None
    intent_retrieval = prepare_source_retrieval(
        run_id=ingress.run_id,
        stage_name="intent",
        attempt=intent_lease.attempt,
        retrieval_origin="intent_web_acquisition",
        source_url="https://example.test/strategy/",
        retrieved_at=now,
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Acquired page</body></html>",
        extracted_text="Acquired page",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )
    repository.persist_source_retrievals(retrievals=[intent_retrieval])
    snapshot = build_intent_input_snapshot(
        run_id=ingress.run_id,
        attempt=intent_lease.attempt,
        materials=repository.load_materials(run_id=ingress.run_id),
        retrievals=repository.load_source_retrievals(
            run_id=ingress.run_id,
            stage_name="intent",
            attempt=intent_lease.attempt,
        ),
    )
    repository.persist_intent_input_snapshot(snapshot=snapshot)
    result = IntentStageExecutor(repository).execute(intent_lease)
    commit_intent_stage(repository, intent_lease, result, now=now + timedelta(seconds=3))
    return ingress, intent_retrieval


def _jury_candidate(run_id: str, **overrides):
    values = dict(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://example.test/about/",
        recorded_at=datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc),
        cached_extract_text="Cached about-page extract",
        parser_route="html/main-text/v1",
        metadata={"discovery_label": "company_site"},
    )
    values.update(overrides)
    return prepare_jury_source_candidate(**values)


def _jury_retrieval(run_id: str):
    return prepare_source_retrieval(
        run_id=run_id,
        stage_name="jury_analysis",
        attempt=1,
        retrieval_origin="jury_web_acquisition",
        source_url="https://news.example.test/coverage/",
        retrieved_at=datetime(2026, 8, 28, 10, 5, tzinfo=timezone.utc),
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Jury-stage fetch</body></html>",
        extracted_text="Jury-stage fetch",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
        response_metadata={"provider": "contract-test", "content_length": 41},
    )


def _jury_step_status(run_id: str):
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, lease_owner, fencing_token
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'jury_analysis' AND attempt = 1
                """,
                (run_id,),
            )
            return cursor.fetchone()


def test_candidates_and_jury_retrieval_attach_to_pending_jury_without_claiming():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
    ingress, intent_retrieval = _queue_pending_jury(
        repository, now=now, lease_owner="jury-candidate-owner"
    )
    cached = _jury_candidate(ingress.run_id)
    seed = _jury_candidate(
        ingress.run_id,
        candidate_origin="jury_live_acquisition_seed",
        source_url="https://news.example.test/coverage/",
        recorded_at=datetime(2026, 8, 28, 10, 1, tzinfo=timezone.utc),
        cached_extract_text="",
        parser_route="none/v1",
        metadata={"discovery_label": "seed_url"},
    )
    jury_retrieval = _jury_retrieval(ingress.run_id)

    first_candidates = repository.persist_jury_source_candidates(candidates=[cached, seed])
    second_candidates = repository.persist_jury_source_candidates(candidates=[cached, seed])
    first_retrievals = repository.persist_source_retrievals(retrievals=[jury_retrieval])
    second_retrievals = repository.persist_source_retrievals(retrievals=[jury_retrieval])
    loaded_candidates = repository.load_jury_source_candidates(
        run_id=ingress.run_id,
        stage_name="jury_analysis",
        attempt=1,
    )
    loaded_jury_retrievals = repository.load_source_retrievals(
        run_id=ingress.run_id,
        stage_name="jury_analysis",
        attempt=1,
    )
    loaded_intent_retrievals = repository.load_source_retrievals(
        run_id=ingress.run_id,
        stage_name="intent",
        attempt=1,
    )
    jury_step = _jury_step_status(ingress.run_id)

    assert first_candidates == second_candidates
    assert first_retrievals == second_retrievals
    assert loaded_candidates == [cached, seed]
    assert loaded_jury_retrievals == [jury_retrieval]
    assert loaded_intent_retrievals == [intent_retrieval]
    assert intent_retrieval.retrieval_id not in {
        retrieval.retrieval_id for retrieval in loaded_jury_retrievals
    }
    assert jury_step[0] == "pending"
    assert jury_step[1] is None
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*), bool_or(cached_extract_text = 'Cached about-page extract'),
                       bool_or(cached_extract_text = '')
                FROM jury_source_candidates
                WHERE run_id = %s
                """,
                (ingress.run_id,),
            )
            stored = cursor.fetchone()
            cursor.execute(
                """
                SELECT stage_name, count(*)
                FROM source_retrievals
                WHERE run_id = %s
                GROUP BY stage_name
                ORDER BY stage_name
                """,
                (ingress.run_id,),
            )
            retrieval_counts = cursor.fetchall()

    assert stored == (2, True, True)
    assert retrieval_counts == [("intent", 1), ("jury_analysis", 1)]


def test_conflicting_candidate_payload_under_the_same_id_is_rejected():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
    ingress, _ = _queue_pending_jury(
        repository, now=now, lease_owner="jury-conflict-owner"
    )
    candidate = _jury_candidate(ingress.run_id)
    repository.persist_jury_source_candidates(candidates=[candidate])
    conflicting = replace(
        candidate,
        canonical_url="https://example.test/other",
        cached_extract_text="different extract",
        cached_extract_sha256="a" * 64,
    )

    with pytest.raises(ValueError, match="different immutable candidate content"):
        repository.persist_jury_source_candidates(candidates=[conflicting])


def test_tampered_cached_extract_is_rejected_on_load():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
    ingress, _ = _queue_pending_jury(
        repository, now=now, lease_owner="jury-tamper-owner"
    )
    candidate = _jury_candidate(ingress.run_id)
    repository.persist_jury_source_candidates(candidates=[candidate])

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE jury_source_candidates
            SET cached_extract_text = 'tampered jury extract'
            WHERE candidate_id = %s
            """,
            (candidate.candidate_id,),
        )

    with pytest.raises(JurySourceCandidateIntegrityError, match="hash mismatch"):
        repository.load_jury_source_candidates(
            run_id=ingress.run_id,
            stage_name="jury_analysis",
            attempt=1,
        )
