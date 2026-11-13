"""Disposable PostgreSQL contracts for immutable Verification input snapshots."""

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
from scan_diagnosis_commit import commit_diagnosis_stage  # noqa: E402
from scan_diagnosis_snapshot import build_diagnosis_input_snapshot  # noqa: E402
from scan_foresight_adapter import ForesightStageExecutor  # noqa: E402
from scan_foresight_commit import commit_foresight_stage  # noqa: E402
from scan_foresight_snapshot import build_foresight_input_snapshot  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_intent_adapter import IntentStageExecutor  # noqa: E402
from scan_intent_commit import commit_intent_stage  # noqa: E402
from scan_intent_snapshot import build_intent_input_snapshot  # noqa: E402
from scan_jury_adapter import JuryStageExecutor  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_jury_commit import commit_jury_stage  # noqa: E402
from scan_jury_snapshot import build_jury_input_snapshot  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import ScanRepository, VerificationInputSnapshotIntegrityError  # noqa: E402
from scan_synthesis_adapter import SynthesisStageExecutor  # noqa: E402
from scan_synthesis_commit import commit_synthesis_stage  # noqa: E402
from scan_synthesis_snapshot import build_synthesis_input_snapshot  # noqa: E402
from scan_verification_snapshot import build_verification_input_snapshot  # noqa: E402


DATABASE_URL = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires the explicitly configured disposable PostgreSQL test database",
)

_TABLES = (
    "verification_input_snapshots, synthesis_input_snapshots, foresight_input_snapshots, "
    "diagnosis_input_snapshots, jury_input_snapshots, jury_source_candidates, "
    "intent_input_snapshots, source_retrievals, scan_checkpoints, scan_materials, "
    "scan_events, scan_steps, scan_runs"
)


@pytest.fixture(autouse=True)
def verification_snapshot_schema():
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
            "009_foresight_input_snapshots.sql",
            "010_synthesis_input_snapshots.sql",
            "011_verification_input_snapshots.sql",
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


def _queue_pending_verification(repository: ScanRepository, *, now: datetime, lease_owner: str):
    ingress = prepare_scan_ingress(
        company_name="Verification Snapshot Contract Test",
        company_url="https://example.test",
        report_text="Submitted context about customer intimacy and autonomy.",
        save_to_database=True,
        uploads=[{"filename": "brief.txt", "content": b"Customer brief"}],
        idempotency_key=f"verification-snapshot-{uuid4()}",
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
    jury_checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="jury_analysis", attempt=jury_lease.attempt
    )
    assert jury_checkpoint is not None
    diagnosis_snapshot = build_diagnosis_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        jury_checkpoint=jury_checkpoint,
    )
    repository.persist_diagnosis_input_snapshot(snapshot=diagnosis_snapshot)
    diagnosis_lease = repository.claim_next_step(
        lease_owner=f"{lease_owner}-diagnosis",
        lease_seconds=30,
        now=now + timedelta(seconds=6),
    )
    assert diagnosis_lease is not None
    diagnosis_result = DiagnosisStageExecutor(repository).execute(diagnosis_lease)
    commit_diagnosis_stage(
        repository, diagnosis_lease, diagnosis_result, now=now + timedelta(seconds=7)
    )
    diagnosis_checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="diagnosis", attempt=diagnosis_lease.attempt
    )
    assert diagnosis_checkpoint is not None
    foresight_snapshot = build_foresight_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        diagnosis_checkpoint=diagnosis_checkpoint,
    )
    repository.persist_foresight_input_snapshot(snapshot=foresight_snapshot)
    foresight_lease = repository.claim_next_step(
        lease_owner=f"{lease_owner}-foresight",
        lease_seconds=30,
        now=now + timedelta(seconds=8),
    )
    assert foresight_lease is not None
    foresight_result = ForesightStageExecutor(repository).execute(foresight_lease)
    commit_foresight_stage(
        repository, foresight_lease, foresight_result, now=now + timedelta(seconds=9)
    )
    foresight_checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="foresight", attempt=foresight_lease.attempt
    )
    assert foresight_checkpoint is not None
    synthesis_snapshot = build_synthesis_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        foresight_checkpoint=foresight_checkpoint,
    )
    repository.persist_synthesis_input_snapshot(snapshot=synthesis_snapshot)
    synthesis_lease = repository.claim_next_step(
        lease_owner=f"{lease_owner}-synthesis",
        lease_seconds=30,
        now=now + timedelta(seconds=10),
    )
    assert synthesis_lease is not None
    synthesis_result = SynthesisStageExecutor(repository).execute(synthesis_lease)
    commit_synthesis_stage(
        repository, synthesis_lease, synthesis_result, now=now + timedelta(seconds=11)
    )
    return ingress, synthesis_lease.attempt


def _verification_step_status(run_id: str):
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, lease_owner
                FROM scan_steps
                WHERE run_id = %s AND stage_name = 'verification' AND attempt = 1
                """,
                (run_id,),
            )
            return cursor.fetchone()


def test_verification_input_snapshot_round_trips_without_claiming_and_hydrates_exact_inputs():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc)
    ingress, synthesis_attempt = _queue_pending_verification(
        repository, now=now, lease_owner="verification-snapshot-owner"
    )
    checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="strategic_synthesis", attempt=synthesis_attempt
    )
    assert checkpoint is not None
    snapshot = build_verification_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        synthesis_checkpoint=checkpoint,
    )

    first = repository.persist_verification_input_snapshot(snapshot=snapshot)
    second = repository.persist_verification_input_snapshot(snapshot=snapshot)
    loaded = repository.load_verification_input_snapshot(
        run_id=ingress.run_id, stage_name="verification", attempt=1
    )
    hydrated = repository.load_verified_verification_inputs(run_id=ingress.run_id, attempt=1)
    verification_step = _verification_step_status(ingress.run_id)

    assert first == second
    assert loaded == snapshot == hydrated[0]
    assert hydrated[1] == checkpoint
    assert verification_step == ("pending", None)
    with psycopg.connect(DATABASE_URL) as connection:
        snapshot_count = connection.execute(
            "SELECT count(*) FROM verification_input_snapshots WHERE run_id = %s",
            (ingress.run_id,),
        ).fetchone()[0]
        stored_manifest = connection.execute(
            "SELECT manifest::text FROM verification_input_snapshots WHERE run_id = %s",
            (ingress.run_id,),
        ).fetchone()[0]
    assert snapshot_count == 1
    assert "Cached about-page extract" not in stored_manifest
    assert "Jury-stage fetch" not in stored_manifest
    assert "Acquired page" not in stored_manifest
    assert "synthesis_result" not in stored_manifest
    assert "foresight_result" not in stored_manifest
    assert "diagnosis_result" not in stored_manifest
    assert "jury_result" not in stored_manifest
    assert "intent_result" not in stored_manifest
    assert "Submitted context" not in stored_manifest


def test_tampered_manifest_and_swapped_synthesis_hash_cannot_hydrate():
    repository = ScanRepository(DATABASE_URL)
    now = datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc)
    ingress, synthesis_attempt = _queue_pending_verification(
        repository, now=now, lease_owner="verification-snapshot-tamper"
    )
    checkpoint = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="strategic_synthesis", attempt=synthesis_attempt
    )
    assert checkpoint is not None
    snapshot = build_verification_input_snapshot(
        run_id=ingress.run_id,
        attempt=1,
        synthesis_checkpoint=checkpoint,
    )
    repository.persist_verification_input_snapshot(snapshot=snapshot)

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "UPDATE verification_input_snapshots SET input_hash = %s WHERE snapshot_id = %s",
            ("0" * 64, snapshot.snapshot_id),
        )
    with pytest.raises(
        VerificationInputSnapshotIntegrityError, match="does not match its deterministic manifest"
    ):
        repository.load_verification_input_snapshot(
            run_id=ingress.run_id, stage_name="verification", attempt=1
        )

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "UPDATE verification_input_snapshots SET input_hash = %s WHERE snapshot_id = %s",
            (snapshot.input_hash, snapshot.snapshot_id),
        )
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET output_hash = %s
            WHERE run_id = %s AND stage_name = 'strategic_synthesis' AND attempt = %s
            """,
            ("e" * 64, ingress.run_id, synthesis_attempt),
        )
    with pytest.raises(
        VerificationInputSnapshotIntegrityError, match="does not match the verified durable inputs"
    ):
        repository.load_verified_verification_inputs(run_id=ingress.run_id, attempt=1)
    assert _verification_step_status(ingress.run_id) == ("pending", None)
