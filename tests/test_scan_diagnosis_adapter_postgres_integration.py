"""Disposable PostgreSQL contract for the prepare-only Diagnosis adapter."""

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

from scan_checkpoints import build_material_ingestion_checkpoint  # noqa: E402
from scan_diagnosis_adapter import DiagnosisStageExecutor  # noqa: E402
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_intent_adapter import IntentStageExecutor  # noqa: E402
from scan_intent_commit import commit_intent_stage  # noqa: E402
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_adapter import JuryStageExecutor  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_commit import commit_jury_stage  # noqa: E402
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import ScanRepository  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "diagnosis_input_snapshots, jury_input_snapshots, jury_source_candidates, "
    "intent_input_snapshots, source_retrievals, scan_checkpoints, scan_materials, "
    "scan_events, scan_steps, scan_runs"
)


@pytest.fixture(autouse=True)
def diagnosis_adapter_schema():
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
        )
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")


def _queue_pending_diagnosis(repository: ScanRepository, *, now: datetime, lease_owner: str):
    ingress = prepare_scan_ingress(
        company_name="Diagnosis Adapter Contract Test",
        company_url="https://example.test",
        report_text="Submitted context about customer intimacy and autonomy.",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
        idempotency_key=f"diagnosis-adapter-{uuid4()}",
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
        lease_owner=f"{lease_owner}-intent",
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
    intent_snapshot = build_intent_input_snapshot(
        run_id=ingress.run_id,
        attempt=intent_lease.attempt,
        materials=repository.load_materials(run_id=ingress.run_id),
        retrievals=repository.load_source_retrievals(
            run_id=ingress.run_id,
            stage_name="intent",
            attempt=intent_lease.attempt,
        ),
    )
    repository.persist_intent_input_snapshot(snapshot=intent_snapshot)
    intent_result = IntentStageExecutor(repository).execute(intent_lease)
    commit_intent_stage(repository, intent_lease, intent_result, now=now + timedelta(seconds=3))

    candidate = prepare_jury_source_candidate(
        run_id=ingress.run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://example.test/about/",
        recorded_at=now,
        cached_extract_text="Cached about-page extract",
        parser_route="html/main-text/v1",
        metadata={"discovery_label": "company_site"},
    )
    jury_retrieval = prepare_source_retrieval(
        run_id=ingress.run_id,
        stage_name="jury_analysis",
        attempt=1,
        retrieval_origin="jury_web_acquisition",
        source_url="https://news.example.test/coverage/",
        retrieved_at=now,
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Jury-stage fetch</body></html>",
        extracted_text="Jury-stage fetch",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )
    repository.persist_jury_source_candidates(candidates=[candidate])
    repository.persist_source_retrievals(retrievals=[jury_retrieval])
    intent_checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="intent", attempt=intent_lease.attempt
    )
    assert intent_checkpoint is not None
    jury_snapshot = build_jury_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        intent_checkpoint=intent_checkpoint,
        candidates=repository.load_jury_source_candidates(
            run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
        ),
        retrievals=repository.load_source_retrievals(
            run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
        ),
    )
    repository.persist_jury_input_snapshot(snapshot=jury_snapshot)
    jury_lease = repository.claim_next_step(
        lease_owner=f"{lease_owner}-jury",
        lease_seconds=30,
        now=now + timedelta(seconds=4),
    )
    assert jury_lease is not None
    jury_result = JuryStageExecutor(repository).execute(jury_lease)
    commit_jury_stage(repository, jury_lease, jury_result, now=now + timedelta(seconds=5))
    return ingress, jury_lease.attempt


def test_adapter_reads_verified_inputs_but_does_not_commit_or_queue_next_stage():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    ingress, jury_attempt = _queue_pending_diagnosis(
        repository, now=now, lease_owner="diagnosis-adapter-owner"
    )
    jury_checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="jury_analysis", attempt=jury_attempt
    )
    assert jury_checkpoint is not None
    snapshot = build_diagnosis_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        jury_checkpoint=jury_checkpoint,
    )
    repository.persist_diagnosis_input_snapshot(snapshot=snapshot)
    diagnosis_lease = repository.claim_next_step(
        lease_owner="diagnosis-adapter-diagnosis",
        lease_seconds=30,
        now=now + timedelta(seconds=6),
    )
    assert diagnosis_lease is not None
    assert diagnosis_lease.stage_name == "diagnosis"

    result = DiagnosisStageExecutor(repository).execute(diagnosis_lease)

    assert result.checkpoint is not None
    assert result.next_stage == "foresight"
    assert result.gate_result["status"] == "passed"
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
                WHERE run_id = %s AND stage_name = 'diagnosis' AND attempt = %s
                """,
                (ingress.run_id, diagnosis_lease.attempt),
            )
            step = cursor.fetchone()
            cursor.execute(
                """
                SELECT count(*)
                FROM scan_checkpoints
                WHERE run_id = %s AND stage_name = 'diagnosis'
                """,
                (ingress.run_id,),
            )
            diagnosis_checkpoint_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*)
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'foresight'
                """,
                (ingress.run_id,),
            )
            foresight_step_count = cursor.fetchone()[0]

    assert run == ("running", "diagnosis")
    assert step == ("leased",)
    assert diagnosis_checkpoint_count == 0
    assert foresight_step_count == 0
