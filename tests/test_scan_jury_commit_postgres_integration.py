"""Disposable PostgreSQL contracts for Jury fenced commit and reclaim."""

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
from scan_jury_adapter import JuryStageExecutor, analyze_jury_lanes  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_checkpoint import build_jury_checkpoint, build_jury_input_projection  # noqa: E402
from scan_jury_commit import commit_jury_stage  # noqa: E402
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import (  # noqa: E402
    JuryInputSnapshotIntegrityError,
    JurySourceCandidateIntegrityError,
    ScanRepository,
    SourceRetrievalIntegrityError,
    StageExecutionResult,
    StaleLeaseError,
)


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
def jury_commit_schema():
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


def _prepare_leased_jury(repository: ScanRepository, *, now: datetime, lease_owner: str):
    ingress = prepare_scan_ingress(
        company_name="Jury Commit Contract Test",
        company_url="https://example.test",
        report_text="Submitted context about customer intimacy and autonomy.",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
        idempotency_key=f"jury-commit-{uuid4()}",
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
        lease_owner=lease_owner,
        lease_seconds=30,
        now=now + timedelta(seconds=4),
    )
    assert jury_lease is not None
    assert jury_lease.stage_name == "jury_analysis"
    return ingress, jury_lease


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
                WHERE run_id = %s AND stage_name = 'jury_analysis' AND attempt = 1
                """,
                (run_id,),
            )
            jury_step = cursor.fetchone()
            cursor.execute(
                """
                SELECT status
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'diagnosis' AND attempt = 1
                """,
                (run_id,),
            )
            diagnosis_step = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM scan_checkpoints WHERE run_id = %s AND stage_name = 'jury_analysis'",
                (run_id,),
            )
            jury_checkpoints = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*) FROM scan_events
                WHERE run_id = %s AND stage_name = 'jury_analysis' AND event_type = 'step_completed'
                """,
                (run_id,),
            )
            completed_events = cursor.fetchone()[0]
    return run, jury_step, diagnosis_step, jury_checkpoints, completed_events


def test_passed_jury_commit_queues_diagnosis_without_extracted_text():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    ingress, jury_lease = _prepare_leased_jury(
        repository, now=now, lease_owner="jury-commit-owner"
    )
    result = JuryStageExecutor(repository).execute(jury_lease)

    committed = commit_jury_stage(
        repository, jury_lease, result, now=now + timedelta(seconds=5)
    )
    stored = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="jury_analysis", attempt=jury_lease.attempt
    )
    run, jury_step, diagnosis_step, jury_checkpoints, completed_events = _run_state(ingress.run_id)

    assert committed == result.checkpoint
    assert stored is not None
    assert stored.checkpoint_id == committed.checkpoint_id
    assert stored.input_hash == committed.input_hash
    assert stored.output_hash == committed.output_hash
    assert stored.gate_result["status"] == "passed"
    assert run == ("running", "diagnosis")
    assert jury_step == ("completed", committed.checkpoint_ref, committed.output_hash)
    assert diagnosis_step == ("pending",)
    assert jury_checkpoints == 1
    assert completed_events == 1
    snapshot_text = str(stored.snapshot)
    assert "extracted_text" not in snapshot_text
    assert "Cached about-page extract" not in snapshot_text
    assert "Jury-stage fetch" not in snapshot_text
    assert "intent_result" not in snapshot_text


def test_stale_jury_owner_cannot_commit_and_reclaiming_owner_can():
    repository = ScanRepository(DATABASE_URL)
    start = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    ingress, first = _prepare_leased_jury(
        repository, now=start, lease_owner="jury-before-restart"
    )
    first_result = JuryStageExecutor(repository).execute(first)
    reclaimed = repository.claim_next_step(
        lease_owner="jury-after-restart",
        lease_seconds=30,
        now=start + timedelta(seconds=35),
    )
    assert reclaimed is not None
    assert reclaimed.stage_name == "jury_analysis"
    assert reclaimed.fencing_token == first.fencing_token + 1

    with pytest.raises(StaleLeaseError):
        commit_jury_stage(
            repository, first, first_result, now=start + timedelta(seconds=36)
        )

    run, jury_step, diagnosis_step, jury_checkpoints, _ = _run_state(ingress.run_id)
    assert run == ("running", "jury_analysis")
    assert jury_step[0] == "leased"
    assert diagnosis_step is None
    assert jury_checkpoints == 0

    reclaimed_result = JuryStageExecutor(repository).execute(reclaimed)
    committed = commit_jury_stage(
        repository, reclaimed, reclaimed_result, now=start + timedelta(seconds=37)
    )
    run, jury_step, diagnosis_step, jury_checkpoints, _ = _run_state(ingress.run_id)

    assert committed.checkpoint_id == first_result.checkpoint.checkpoint_id
    assert run == ("running", "diagnosis")
    assert jury_step[0] == "completed"
    assert diagnosis_step == ("pending",)
    assert jury_checkpoints == 1


def test_replay_of_the_same_passed_checkpoint_is_idempotent():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    ingress, jury_lease = _prepare_leased_jury(
        repository, now=now, lease_owner="jury-replay-owner"
    )
    result = JuryStageExecutor(repository).execute(jury_lease)
    first = commit_jury_stage(
        repository, jury_lease, result, now=now + timedelta(seconds=5)
    )
    second = commit_jury_stage(
        repository, jury_lease, result, now=now + timedelta(seconds=6)
    )
    run, jury_step, diagnosis_step, jury_checkpoints, completed_events = _run_state(ingress.run_id)

    assert first == second
    assert run == ("running", "diagnosis")
    assert jury_step[0] == "completed"
    assert diagnosis_step == ("pending",)
    assert jury_checkpoints == 1
    assert completed_events == 1


def test_malformed_jury_proposal_cannot_complete_the_step():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    ingress, jury_lease = _prepare_leased_jury(
        repository, now=now, lease_owner="jury-invalid-owner"
    )
    snapshot, intent_checkpoint, candidates, retrievals = repository.load_verified_jury_inputs(
        run_id=ingress.run_id,
        attempt=jury_lease.attempt,
    )
    projection = build_jury_input_projection(
        snapshot,
        intent_checkpoint=intent_checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )
    failed = build_jury_checkpoint(projection, jury_result={"findings": []})

    with pytest.raises(ValueError, match="passed Jury gate"):
        commit_jury_stage(
            repository,
            jury_lease,
            StageExecutionResult(
                output_hash=failed.output_hash,
                checkpoint_ref=failed.checkpoint_ref,
                gate_result=failed.gate_result,
                next_stage=None,
                checkpoint=failed,
            ),
            now=now + timedelta(seconds=5),
        )
    run, jury_step, diagnosis_step, jury_checkpoints, completed_events = _run_state(ingress.run_id)
    assert run == ("running", "jury_analysis")
    assert jury_step[0] == "leased"
    assert diagnosis_step is None
    assert jury_checkpoints == 0
    assert completed_events == 0


def test_restart_reload_keeps_jury_hashes_and_does_not_skip_the_gate():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    ingress, jury_lease = _prepare_leased_jury(
        repository, now=now, lease_owner="jury-restart-owner"
    )
    result = JuryStageExecutor(repository).execute(jury_lease)
    commit_jury_stage(repository, jury_lease, result, now=now + timedelta(seconds=5))

    snapshot, intent_checkpoint, candidates, retrievals = repository.load_verified_jury_inputs(
        run_id=ingress.run_id,
        attempt=jury_lease.attempt,
    )
    projection = build_jury_input_projection(
        snapshot,
        intent_checkpoint=intent_checkpoint,
        candidates=candidates,
        retrievals=retrievals,
    )
    rebuilt = build_jury_checkpoint(
        projection, jury_result=analyze_jury_lanes(projection)
    )
    stored = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="jury_analysis", attempt=jury_lease.attempt
    )
    run, _, diagnosis_step, _, _ = _run_state(ingress.run_id)

    assert stored is not None
    assert rebuilt.input_hash == stored.input_hash == result.checkpoint.input_hash
    assert rebuilt.output_hash == stored.output_hash == result.checkpoint.output_hash
    assert stored.gate_result["status"] == "passed"
    assert diagnosis_step == ("pending",)
    assert run == ("running", "diagnosis")
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE jury_source_candidates
            SET cached_extract_text = 'tampered after jury commit'
            WHERE run_id = %s
            """,
            (ingress.run_id,),
        )

    with pytest.raises(
        (JurySourceCandidateIntegrityError, JuryInputSnapshotIntegrityError, SourceRetrievalIntegrityError)
    ):
        repository.load_verified_jury_inputs(
            run_id=ingress.run_id,
            attempt=jury_lease.attempt,
        )
