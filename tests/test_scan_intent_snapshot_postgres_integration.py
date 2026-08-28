"""Disposable PostgreSQL contracts for immutable Intent input snapshots."""

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
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import IntentInputSnapshotIntegrityError, ScanRepository  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)


@pytest.fixture(autouse=True)
def intent_snapshot_schema():
    migrations = [
        (_BACKEND / "migrations" / name).read_text(encoding="utf-8")
        for name in (
            "001_scan_execution_state.sql",
            "002_scan_materials.sql",
            "003_scan_checkpoints.sql",
            "004_source_retrievals.sql",
            "005_intent_input_snapshots.sql",
        )
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS intent_input_snapshots, source_retrievals, scan_checkpoints, scan_materials, scan_events, scan_steps, scan_runs CASCADE"
        )
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS intent_input_snapshots, source_retrievals, scan_checkpoints, scan_materials, scan_events, scan_steps, scan_runs CASCADE"
        )


def _leased_intent_step(repository: ScanRepository):
    ingress = prepare_scan_ingress(
        company_name="Intent Snapshot Contract Test",
        company_url="https://example.test",
        report_text="Submitted context",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
        idempotency_key=f"intent-snapshot-{uuid4()}",
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
    now = datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc)
    material_lease = repository.claim_next_step(
        lease_owner="intent-snapshot-material", lease_seconds=30, now=now
    )
    assert material_lease is not None
    checkpoint = build_material_ingestion_checkpoint(
        run_id=ingress.run_id, attempt=material_lease.attempt, materials=repository.load_materials(run_id=ingress.run_id)
    )
    repository.complete_step_with_checkpoint(
        material_lease, checkpoint=checkpoint, next_stage="intent", now=now + timedelta(seconds=1)
    )
    intent_lease = repository.claim_next_step(
        lease_owner="intent-snapshot-intent", lease_seconds=30, now=now + timedelta(seconds=2)
    )
    assert intent_lease is not None
    return ingress, intent_lease


def _retrieval(run_id: str, attempt: int):
    return prepare_source_retrieval(
        run_id=run_id,
        stage_name="intent",
        attempt=attempt,
        retrieval_origin="intent_web_acquisition",
        source_url="https://example.test/strategy/",
        retrieved_at=datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc),
        http_status=200,
        content_type="text/html",
        response_content=b"<html><body>Acquired page</body></html>",
        extracted_text="Acquired page",
        parser_route="html/main-text/v1",
        readability_status="readable",
        retrieval_outcome="succeeded",
    )


def test_intent_input_snapshot_round_trips_idempotently_and_hydrates_exact_inputs():
    repository = ScanRepository(DATABASE_URL)
    ingress, lease = _leased_intent_step(repository)
    retrieval = _retrieval(ingress.run_id, lease.attempt)
    repository.persist_source_retrievals(retrievals=[retrieval])
    materials = repository.load_materials(run_id=ingress.run_id)
    retrievals = repository.load_source_retrievals(
        run_id=ingress.run_id, stage_name="intent", attempt=lease.attempt
    )
    snapshot = build_intent_input_snapshot(
        run_id=ingress.run_id, attempt=lease.attempt, materials=materials, retrievals=retrievals
    )

    first = repository.persist_intent_input_snapshot(snapshot=snapshot)
    second = repository.persist_intent_input_snapshot(snapshot=snapshot)
    loaded = repository.load_intent_input_snapshot(
        run_id=ingress.run_id, stage_name="intent", attempt=lease.attempt
    )
    hydrated_snapshot, hydrated_materials, hydrated_retrievals = repository.load_verified_intent_inputs(
        run_id=ingress.run_id, attempt=lease.attempt
    )

    assert first == second
    assert loaded == snapshot == hydrated_snapshot
    assert hydrated_materials == materials
    assert hydrated_retrievals == retrievals
    with psycopg.connect(DATABASE_URL) as connection:
        stored = connection.execute(
            "SELECT count(*), manifest::text FROM intent_input_snapshots WHERE run_id = %s",
            (ingress.run_id,),
        ).fetchone()
    assert stored[0] == 1
    assert "extracted_text" not in stored[1]
    assert "response_content" not in stored[1]


def test_tampered_manifest_is_rejected_before_any_future_intent_adapter_can_use_it():
    repository = ScanRepository(DATABASE_URL)
    ingress, lease = _leased_intent_step(repository)
    snapshot = build_intent_input_snapshot(
        run_id=ingress.run_id,
        attempt=lease.attempt,
        materials=repository.load_materials(run_id=ingress.run_id),
        retrievals=[],
    )
    repository.persist_intent_input_snapshot(snapshot=snapshot)
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "UPDATE intent_input_snapshots SET input_hash = %s WHERE snapshot_id = %s",
            ("0" * 64, snapshot.snapshot_id),
        )

    with pytest.raises(IntentInputSnapshotIntegrityError, match="does not match its deterministic manifest"):
        repository.load_intent_input_snapshot(
            run_id=ingress.run_id, stage_name="intent", attempt=lease.attempt
        )
