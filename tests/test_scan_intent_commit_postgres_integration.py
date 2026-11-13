"""Disposable PostgreSQL contracts for Intent fenced commit and reclaim."""

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
from scan_intent_checkpoint import build_intent_checkpoint, build_intent_input_projection  # noqa: E402
from scan_intent_commit import commit_intent_stage  # noqa: E402
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import (  # noqa: E402
    IntentInputSnapshotIntegrityError,
    MaterialIntegrityError,
    ScanRepository,
    StageExecutionResult,
    StaleLeaseError,
)
from scanner_one import analyze_intent  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)


@pytest.fixture(autouse=True)
def intent_commit_schema():
    migrations = [
        (_BACKEND / "migrations" / name).read_text(encoding="utf-8")
        for name in (
            "001_scan_execution_state.sql",
            "002_scan_materials.sql",
            "003_scan_checkpoints.sql",
            "004_source_retrievals.sql",
            "005_intent_input_snapshots.sql",
            "012_scan_runs_save_to_database.sql",
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


def _prepare_leased_intent(repository: ScanRepository, *, now: datetime, lease_owner: str):
    ingress = prepare_scan_ingress(
        company_name="Intent Commit Contract Test",
        company_url="https://example.test",
        report_text="Submitted context about customer intimacy and autonomy.",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
        idempotency_key=f"intent-commit-{uuid4()}",
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
    retrieval = prepare_source_retrieval(
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
    repository.persist_source_retrievals(retrievals=[retrieval])
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
    return ingress, intent_lease


def _run_state(run_id: str):
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, current_stage FROM scan_runs WHERE run_id = %s",
                (run_id,),
            )
            run = cursor.fetchone()
            cursor.execute(
                """
                SELECT status, checkpoint_ref, output_hash
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'intent' AND attempt = 1
                """,
                (run_id,),
            )
            intent_step = cursor.fetchone()
            cursor.execute(
                """
                SELECT status
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'jury_analysis' AND attempt = 1
                """,
                (run_id,),
            )
            jury_step = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM scan_checkpoints WHERE run_id = %s AND stage_name = 'intent'",
                (run_id,),
            )
            intent_checkpoints = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*) FROM scan_events
                WHERE run_id = %s AND stage_name = 'intent' AND event_type = 'step_completed'
                """,
                (run_id,),
            )
            completed_events = cursor.fetchone()[0]
    return run, intent_step, jury_step, intent_checkpoints, completed_events


def test_passed_intent_commit_queues_jury_analysis_without_extracted_text():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    ingress, intent_lease = _prepare_leased_intent(
        repository, now=now, lease_owner="intent-commit-owner"
    )
    result = IntentStageExecutor(repository).execute(intent_lease)

    committed = commit_intent_stage(
        repository, intent_lease, result, now=now + timedelta(seconds=3)
    )
    stored = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="intent", attempt=intent_lease.attempt
    )
    run, intent_step, jury_step, intent_checkpoints, completed_events = _run_state(ingress.run_id)

    assert committed == result.checkpoint
    assert stored is not None
    assert stored.checkpoint_id == committed.checkpoint_id
    assert stored.input_hash == committed.input_hash
    assert stored.output_hash == committed.output_hash
    assert stored.gate_result["status"] == "passed"
    assert run == ("running", "jury_analysis")
    assert intent_step == ("completed", committed.checkpoint_ref, committed.output_hash)
    assert jury_step == ("pending",)
    assert intent_checkpoints == 1
    assert completed_events == 1
    snapshot_text = str(stored.snapshot)
    assert "extracted_text" not in snapshot_text
    assert "Acquired page" not in snapshot_text
    assert "Submitted context about customer intimacy" not in snapshot_text


def test_stale_intent_owner_cannot_commit_and_reclaiming_owner_can():
    repository = ScanRepository(DATABASE_URL)
    start = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    ingress, first = _prepare_leased_intent(
        repository, now=start, lease_owner="intent-before-restart"
    )
    first_result = IntentStageExecutor(repository).execute(first)
    reclaimed = repository.claim_next_step(
        lease_owner="intent-after-restart",
        lease_seconds=30,
        now=start + timedelta(seconds=33),
    )
    assert reclaimed is not None
    assert reclaimed.stage_name == "intent"
    assert reclaimed.fencing_token == first.fencing_token + 1

    with pytest.raises(StaleLeaseError):
        commit_intent_stage(
            repository, first, first_result, now=start + timedelta(seconds=34)
        )

    run, intent_step, jury_step, intent_checkpoints, _ = _run_state(ingress.run_id)
    assert run == ("running", "intent")
    assert intent_step[0] == "leased"
    assert jury_step is None
    assert intent_checkpoints == 0

    reclaimed_result = IntentStageExecutor(repository).execute(reclaimed)
    committed = commit_intent_stage(
        repository, reclaimed, reclaimed_result, now=start + timedelta(seconds=35)
    )
    run, intent_step, jury_step, intent_checkpoints, _ = _run_state(ingress.run_id)

    assert committed.checkpoint_id == first_result.checkpoint.checkpoint_id
    assert run == ("running", "jury_analysis")
    assert intent_step[0] == "completed"
    assert jury_step == ("pending",)
    assert intent_checkpoints == 1


def test_replay_of_the_same_passed_checkpoint_is_idempotent():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    ingress, intent_lease = _prepare_leased_intent(
        repository, now=now, lease_owner="intent-replay-owner"
    )
    result = IntentStageExecutor(repository).execute(intent_lease)
    first = commit_intent_stage(
        repository, intent_lease, result, now=now + timedelta(seconds=3)
    )
    second = commit_intent_stage(
        repository, intent_lease, result, now=now + timedelta(seconds=4)
    )
    run, intent_step, jury_step, intent_checkpoints, completed_events = _run_state(ingress.run_id)

    assert first == second
    assert run == ("running", "jury_analysis")
    assert intent_step[0] == "completed"
    assert jury_step == ("pending",)
    assert intent_checkpoints == 1
    assert completed_events == 1


def test_malformed_intent_proposal_cannot_complete_the_step():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    ingress, intent_lease = _prepare_leased_intent(
        repository, now=now, lease_owner="intent-invalid-owner"
    )
    snapshot, materials, retrievals = repository.load_verified_intent_inputs(
        run_id=ingress.run_id,
        attempt=intent_lease.attempt,
    )
    projection = build_intent_input_projection(
        snapshot, materials=materials, retrievals=retrievals
    )
    failed = build_intent_checkpoint(projection, intent_result={"score": 12})

    with pytest.raises(ValueError, match="passed Intent gate"):
        commit_intent_stage(
            repository,
            intent_lease,
            StageExecutionResult(
                output_hash=failed.output_hash,
                checkpoint_ref=failed.checkpoint_ref,
                gate_result=failed.gate_result,
                next_stage=None,
                checkpoint=failed,
            ),
            now=now + timedelta(seconds=3),
        )
    run, intent_step, jury_step, intent_checkpoints, completed_events = _run_state(ingress.run_id)
    assert run == ("running", "intent")
    assert intent_step[0] == "leased"
    assert jury_step is None
    assert intent_checkpoints == 0
    assert completed_events == 0


def test_restart_reload_keeps_intent_hashes_and_does_not_skip_the_gate():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)
    ingress, intent_lease = _prepare_leased_intent(
        repository, now=now, lease_owner="intent-restart-owner"
    )
    result = IntentStageExecutor(repository).execute(intent_lease)
    commit_intent_stage(repository, intent_lease, result, now=now + timedelta(seconds=3))

    snapshot, materials, retrievals = repository.load_verified_intent_inputs(
        run_id=ingress.run_id,
        attempt=intent_lease.attempt,
    )
    projection = build_intent_input_projection(
        snapshot, materials=materials, retrievals=retrievals
    )
    rebuilt = build_intent_checkpoint(
        projection, intent_result=analyze_intent(projection.concatenated_text)
    )
    stored = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="intent", attempt=intent_lease.attempt
    )
    run, _, jury_step, _, _ = _run_state(ingress.run_id)

    assert stored is not None
    assert rebuilt.input_hash == stored.input_hash == result.checkpoint.input_hash
    assert rebuilt.output_hash == stored.output_hash == result.checkpoint.output_hash
    assert stored.gate_result["status"] == "passed"
    assert jury_step == ("pending",)
    assert run == ("running", "jury_analysis")
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_materials
            SET extracted_text = 'tampered after intent commit'
            WHERE run_id = %s AND origin = 'uploaded_file'
            """,
            (ingress.run_id,),
        )

    with pytest.raises((MaterialIntegrityError, IntentInputSnapshotIntegrityError)):
        repository.load_verified_intent_inputs(
            run_id=ingress.run_id,
            attempt=intent_lease.attempt,
        )
