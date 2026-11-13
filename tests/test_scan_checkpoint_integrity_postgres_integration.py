"""Disposable PostgreSQL contracts for Card 3K-integrity:
1. `load_checkpoint()` payload-hash, checkpoint-ID, embedded-gate, and ref verification.
2. Durable `save_to_database` persistence and reload on `scan_runs`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_durable_loop import DURABLE_STAGE_ORDER, drive_durable_chain  # noqa: E402
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_supervisor import (  # noqa: E402
    CheckpointIntegrityError,
    ForesightInputSnapshotIntegrityError,
    ScanRepository,
)


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

_ALL_MIGRATIONS = (
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


@pytest.fixture(autouse=True)
def checkpoint_integrity_schema():
    migrations = [
        (_BACKEND / "migrations" / name).read_text(encoding="utf-8")
        for name in _ALL_MIGRATIONS
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
        for migration in migrations:
            connection.execute(migration)
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _persist_ingress(repository: ScanRepository, *, save_to_database: bool, key_suffix: str):
    ingress = prepare_scan_ingress(
        company_name="Integrity Contract Oy",
        company_url="https://example.test",
        report_text="Submitted context on platform modernization and customer autonomy.",
        save_to_database=save_to_database,
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided brief"}],
        idempotency_key=f"card-3k-{key_suffix}-{uuid4()}",
    )
    created = repository.create_run_with_materials(
        run_id=ingress.run_id,
        company_name=ingress.company_name,
        company_url=ingress.company_url,
        idempotency_key=ingress.idempotency_key,
        request_hash=ingress.request_hash,
        cognitive_contract_version=ingress.cognitive_contract_version,
        initial_stage=ingress.initial_stage,
        materials=list(ingress.materials),
        save_to_database=ingress.save_to_database,
    )
    return ingress, created


def test_full_chain_reloads_all_checkpoints_and_preserves_save_to_database_false():
    repository = ScanRepository(DATABASE_URL)
    ingress, created = _persist_ingress(
        repository, save_to_database=False, key_suffix="full-chain-false"
    )
    assert created["save_to_database"] is False

    queued_run = repository.load_run(run_id=ingress.run_id)
    assert queued_run is not None
    assert queued_run.save_to_database is False
    assert queued_run.status == "queued"

    chain = drive_durable_chain(
        repository,
        lease_owner="integrity-owner-a",
        now=datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc),
        lease_seconds=30,
        reclaim_stage="foresight",
        replacement_owner="integrity-owner-b",
    )
    assert chain.stages == DURABLE_STAGE_ORDER

    for stage_name in DURABLE_STAGE_ORDER:
        reloaded = repository.load_checkpoint(
            run_id=ingress.run_id,
            stage_name=stage_name,
            attempt=1,
        )
        assert reloaded is not None
        assert reloaded.output_hash == _canonical_hash(reloaded.snapshot)
        assert reloaded.gate_result["status"] == "passed"

    completed_run = repository.load_run(run_id=ingress.run_id)
    assert completed_run is not None
    assert completed_run.status == "completed"
    assert completed_run.current_stage is None
    assert completed_run.save_to_database is False
    assert completed_run.final_artifact_ref is None


def test_in_place_jsonb_snapshot_mutation_is_rejected_on_reload_and_blocks_downstream_hydration():
    repository = ScanRepository(DATABASE_URL)
    ingress, _ = _persist_ingress(
        repository, save_to_database=True, key_suffix="jsonb-tamper"
    )
    drive_durable_chain(
        repository,
        lease_owner="integrity-owner",
        now=datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc),
        lease_seconds=30,
    )

    original_diagnosis = repository.load_checkpoint(
        run_id=ingress.run_id, stage_name="diagnosis", attempt=1
    )
    assert original_diagnosis is not None

    # 1. Mutate diagnosis_result inside JSONB while leaving output_hash untouched.
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET snapshot = jsonb_set(snapshot, '{diagnosis_result,reality_score}', '99'::jsonb)
            WHERE run_id = %s AND stage_name = 'diagnosis' AND attempt = 1
            """,
            (ingress.run_id,),
        )

    with pytest.raises(CheckpointIntegrityError, match="output_refs do not match"):
        repository.load_checkpoint(
            run_id=ingress.run_id, stage_name="diagnosis", attempt=1
        )
    with pytest.raises(
        ForesightInputSnapshotIntegrityError,
        match="does not match the verified durable inputs",
    ) as exc_info:
        repository.load_verified_foresight_inputs(run_id=ingress.run_id, attempt=1)
    assert isinstance(exc_info.value.__cause__, CheckpointIntegrityError)

    # 2. Mutate diagnosis_result, output_refs, AND output_hash without updating checkpoint_id.
    forged_snapshot = json.loads(json.dumps(original_diagnosis.snapshot))
    forged_snapshot["diagnosis_result"]["reality_score"] = 99
    forged_result_hash = _canonical_hash(forged_snapshot["diagnosis_result"])
    forged_snapshot["output_refs"] = [
        {
            "id": "DRO-"
            + _canonical_hash(
                {
                    "run_id": ingress.run_id,
                    "stage": "diagnosis",
                    "attempt": 1,
                    "sha256": forged_result_hash,
                }
            )[:32].upper(),
            "sha256": forged_result_hash,
            "kind": "diagnosis_result",
        }
    ]
    forged_output_hash = _canonical_hash(forged_snapshot)

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET snapshot = %s::jsonb, output_hash = %s
            WHERE run_id = %s AND stage_name = 'diagnosis' AND attempt = 1
            """,
            (json.dumps(forged_snapshot), forged_output_hash, ingress.run_id),
        )

    with pytest.raises(CheckpointIntegrityError, match="checkpoint_id mismatch"):
        repository.load_checkpoint(
            run_id=ingress.run_id, stage_name="diagnosis", attempt=1
        )
    with pytest.raises(
        ForesightInputSnapshotIntegrityError,
        match="does not match the verified durable inputs",
    ):
        repository.load_verified_foresight_inputs(run_id=ingress.run_id, attempt=1)

    # 3. Mutate gate_result column without matching embedded gate_result on material_ingestion.
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET gate_result = jsonb_set(gate_result, '{warnings}', '[{"code":"tampered"}]'::jsonb)
            WHERE run_id = %s AND stage_name = 'material_ingestion' AND attempt = 1
            """,
            (ingress.run_id,),
        )

    with pytest.raises(CheckpointIntegrityError, match="embedded gate_result"):
        repository.load_checkpoint(
            run_id=ingress.run_id, stage_name="material_ingestion", attempt=1
        )


def test_save_to_database_idempotency_conflict_and_failure_survival():
    repository = ScanRepository(DATABASE_URL)
    shared_key = f"card-3k-idemp-{uuid4()}"
    first_false = prepare_scan_ingress(
        company_name="Idempotent Flag Test",
        company_url="https://example.test",
        report_text="Submitted context",
        save_to_database=False,
        uploads=[],
        idempotency_key=shared_key,
    )
    flipped_true = prepare_scan_ingress(
        company_name="Idempotent Flag Test",
        company_url="https://example.test",
        report_text="Submitted context",
        save_to_database=True,
        uploads=[],
        idempotency_key=shared_key,
    )

    created = repository.create_run_with_materials(
        run_id=first_false.run_id,
        company_name=first_false.company_name,
        company_url=first_false.company_url,
        idempotency_key=first_false.idempotency_key,
        request_hash=first_false.request_hash,
        cognitive_contract_version=first_false.cognitive_contract_version,
        initial_stage=first_false.initial_stage,
        materials=list(first_false.materials),
        save_to_database=first_false.save_to_database,
    )
    replayed = repository.create_run_with_materials(
        run_id=first_false.run_id,
        company_name=first_false.company_name,
        company_url=first_false.company_url,
        idempotency_key=first_false.idempotency_key,
        request_hash=first_false.request_hash,
        cognitive_contract_version=first_false.cognitive_contract_version,
        initial_stage=first_false.initial_stage,
        materials=list(first_false.materials),
        save_to_database=first_false.save_to_database,
    )
    assert created == replayed
    assert created["save_to_database"] is False

    with pytest.raises(ValueError, match="different scan input"):
        repository.create_run_with_materials(
            run_id=flipped_true.run_id,
            company_name=flipped_true.company_name,
            company_url=flipped_true.company_url,
            idempotency_key=flipped_true.idempotency_key,
            request_hash=flipped_true.request_hash,
            cognitive_contract_version=flipped_true.cognitive_contract_version,
            initial_stage=flipped_true.initial_stage,
            materials=list(flipped_true.materials),
            save_to_database=flipped_true.save_to_database,
        )

    now = datetime(2026, 11, 13, 11, 0, tzinfo=timezone.utc)
    lease = repository.claim_next_step(
        lease_owner="flag-worker-1",
        lease_seconds=30,
        now=now,
    )
    assert lease is not None
    assert repository.heartbeat(lease, lease_seconds=30, now=now + timedelta(seconds=10)) is True
    repository.fail_step(
        lease,
        retryable=True,
        reason="transient provider timeout",
        now=now + timedelta(seconds=15),
    )

    reclaimed = repository.claim_next_step(
        lease_owner="flag-worker-2",
        lease_seconds=30,
        now=now + timedelta(seconds=16),
    )
    assert reclaimed is not None
    repository.fail_step(
        reclaimed,
        retryable=False,
        reason="terminal test failure",
        now=now + timedelta(seconds=17),
    )

    failed_run = repository.load_run(run_id=first_false.run_id)
    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.save_to_database is False


def test_migration_012_is_idempotent_and_backfills_legacy_scan_runs_to_true():
    migration_001 = (_BACKEND / "migrations" / "001_scan_execution_state.sql").read_text(
        encoding="utf-8"
    )
    migration_012 = (_BACKEND / "migrations" / "012_scan_runs_save_to_database.sql").read_text(
        encoding="utf-8"
    )
    legacy_run_id = f"RUN-LEGACY-{uuid4()}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
        connection.execute(migration_001)
        connection.execute(
            """
            INSERT INTO scan_runs (
                run_id, company_name, company_url, cognitive_contract_version,
                status, current_stage, idempotency_key, request_hash
            ) VALUES (%s, %s, %s, %s, 'queued', 'material_ingestion', %s, %s)
            """,
            (
                legacy_run_id,
                "Legacy Migration Test",
                "https://example.test",
                "scanner-cognitive/v1",
                f"legacy-key-{uuid4()}",
                "b" * 64,
            ),
        )
        connection.execute(migration_012)
        connection.execute(migration_012)

    repository = ScanRepository(DATABASE_URL)
    migrated = repository.load_run(run_id=legacy_run_id)
    assert migrated is not None
    assert migrated.save_to_database is True
