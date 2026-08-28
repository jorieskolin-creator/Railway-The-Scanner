"""Disposable PostgreSQL contracts for immutable Jury input snapshots."""

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
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_intent_adapter import IntentStageExecutor  # noqa: E402
from scan_intent_commit import commit_intent_stage  # noqa: E402
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import JuryInputSnapshotIntegrityError, ScanRepository  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "jury_input_snapshots, jury_source_candidates, intent_input_snapshots, "
    "source_retrievals, scan_checkpoints, scan_materials, scan_events, "
    "scan_steps, scan_runs"
)


@pytest.fixture(autouse=True)
def jury_snapshot_schema():
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
        company_name="Jury Snapshot Contract Test",
        company_url="https://example.test",
        report_text="Submitted context about customer intimacy and autonomy.",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
        idempotency_key=f"jury-snapshot-{uuid4()}",
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
    return ingress, intent_lease.attempt, intent_retrieval


def _jury_candidate(run_id: str, **overrides):
    values = dict(
        run_id=run_id,
        attempt=1,
        candidate_origin="company_evidence_db",
        source_url="https://example.test/about/",
        recorded_at=datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc),
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
        retrieved_at=datetime(2026, 8, 28, 11, 5, tzinfo=timezone.utc),
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Jury-stage fetch</body></html>",
        extracted_text="Jury-stage fetch",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )


def _attach_jury_inputs(repository: ScanRepository, run_id: str):
    candidate = _jury_candidate(run_id)
    retrieval = _jury_retrieval(run_id)
    repository.persist_jury_source_candidates(candidates=[candidate])
    repository.persist_source_retrievals(retrievals=[retrieval])
    return candidate, retrieval


def _jury_step_status(run_id: str):
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, lease_owner
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'jury_analysis' AND attempt = 1
                """,
                (run_id,),
            )
            return cursor.fetchone()


def test_jury_input_snapshot_round_trips_without_claiming_and_hydrates_exact_inputs():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc)
    ingress, intent_attempt, intent_retrieval = _queue_pending_jury(
        repository, now=now, lease_owner="jury-snapshot-owner"
    )
    candidate, jury_retrieval = _attach_jury_inputs(repository, ingress.run_id)
    checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="intent", attempt=intent_attempt
    )
    assert checkpoint is not None
    snapshot = build_jury_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        intent_checkpoint=checkpoint,
        candidates=repository.load_jury_source_candidates(
            run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
        ),
        retrievals=repository.load_source_retrievals(
            run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
        ),
    )

    first = repository.persist_jury_input_snapshot(snapshot=snapshot)
    second = repository.persist_jury_input_snapshot(snapshot=snapshot)
    loaded = repository.load_jury_input_snapshot(
        run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
    )
    hydrated = repository.load_verified_jury_inputs(run_id=ingress.run_id, attempt=1)
    jury_step = _jury_step_status(ingress.run_id)

    assert first == second
    assert loaded == snapshot == hydrated[0]
    assert hydrated[1] == checkpoint
    assert hydrated[2] == [candidate]
    assert hydrated[3] == [jury_retrieval]
    assert intent_retrieval.retrieval_id not in {
        item.retrieval_id for item in hydrated[3]
    }
    assert jury_step == ("pending", None)
    with psycopg.connect(DATABASE_URL) as connection:
        snapshot_count = connection.execute(
            "SELECT count(*) FROM jury_input_snapshots WHERE run_id = %s",
            (ingress.run_id,),
        ).fetchone()[0]
        stored_manifest = connection.execute(
            "SELECT manifest::text FROM jury_input_snapshots WHERE run_id = %s",
            (ingress.run_id,),
        ).fetchone()[0]
    assert snapshot_count == 1
    assert "Cached about-page extract" not in stored_manifest
    assert "Jury-stage fetch" not in stored_manifest
    assert "Acquired page" not in stored_manifest
    assert "intent_result" not in stored_manifest
    assert "Submitted context" not in stored_manifest


def test_tampered_manifest_and_extra_candidate_cannot_hydrate():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc)
    ingress, intent_attempt, _ = _queue_pending_jury(
        repository, now=now, lease_owner="jury-snapshot-tamper"
    )
    _attach_jury_inputs(repository, ingress.run_id)
    checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="intent", attempt=intent_attempt
    )
    assert checkpoint is not None
    snapshot = build_jury_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        intent_checkpoint=checkpoint,
        candidates=repository.load_jury_source_candidates(
            run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
        ),
        retrievals=repository.load_source_retrievals(
            run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
        ),
    )
    repository.persist_jury_input_snapshot(snapshot=snapshot)

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "UPDATE jury_input_snapshots SET input_hash = %s WHERE snapshot_id = %s",
            ("0" * 64, snapshot.snapshot_id),
        )
    with pytest.raises(JuryInputSnapshotIntegrityError, match="does not match its deterministic manifest"):
        repository.load_jury_input_snapshot(
            run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
        )

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "UPDATE jury_input_snapshots SET input_hash = %s WHERE snapshot_id = %s",
            (snapshot.input_hash, snapshot.snapshot_id),
        )
    extra = _jury_candidate(
        ingress.run_id,
        candidate_origin="previous_scan_source_cache",
        source_url="https://cache.example.test/prior/",
        cached_extract_text="Prior cached extract",
    )
    repository.persist_jury_source_candidates(candidates=[extra])
    with pytest.raises(JuryInputSnapshotIntegrityError, match="does not match the verified durable inputs"):
        repository.load_verified_jury_inputs(run_id=ingress.run_id, attempt=1)
    assert _jury_step_status(ingress.run_id) == ("pending", None)
