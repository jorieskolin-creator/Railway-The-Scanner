"""Disposable PostgreSQL contracts for Card 3J-restart full-chain restart and reclaim matrix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scan_durable_loop import (  # noqa: E402
    DURABLE_STAGE_ORDER,
    commit_prepared_stage,
    prepare_leased_stage,
    resume_durable_chain,
    verify_completed_stage_prefix,
)
from scan_ingress import prepare_scan_ingress  # noqa: E402
from scan_jury_candidates import prepare_jury_source_candidate  # noqa: E402
from scan_retrievals import prepare_source_retrieval  # noqa: E402
from scan_supervisor import (  # noqa: E402
    CheckpointIntegrityError,
    ScanRepository,
    StaleLeaseError,
    StepLease,
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


def _reset_schema() -> None:
    migrations = [
        (_BACKEND / "migrations" / name).read_text(encoding="utf-8")
        for name in _ALL_MIGRATIONS
    ]
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
        for migration in migrations:
            connection.execute(migration)


@pytest.fixture(autouse=True)
def restart_matrix_schema():
    _reset_schema()
    yield
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")


def _create_ingress_run(
    repository: ScanRepository,
    *,
    idempotency_key: str,
    save_to_database: bool,
):
    ingress = prepare_scan_ingress(
        company_name="Restart Matrix Postgres Oy",
        company_url="https://example.test",
        report_text="Submitted context on platform architecture, customer autonomy, and AI governance.",
        save_to_database=save_to_database,
        uploads=[{"filename": "brief.txt", "content": b"Customer-provided architecture brief"}],
        idempotency_key=idempotency_key,
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
        save_to_database=ingress.save_to_database,
    )
    return ingress


def _seed_stage_provenance(repository: ScanRepository, lease: StepLease) -> None:
    fixed_time = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    if lease.stage_name == "intent":
        repository.persist_source_retrievals(
            retrievals=[
                prepare_source_retrieval(
                    run_id=lease.run_id,
                    stage_name="intent",
                    attempt=lease.attempt,
                    retrieval_origin="intent_web_acquisition",
                    source_url="https://example.test/strategy",
                    retrieved_at=fixed_time,
                    http_status=200,
                    content_type="text/html",
                    response_content=b"<html><body>Strategy page</body></html>",
                    extracted_text="Customer autonomy and platform strategy.",
                    parser_route="html/main-text/v1",
                    readability_status="readable",
                    retrieval_outcome="succeeded",
                )
            ]
        )
    elif lease.stage_name == "jury_analysis":
        repository.persist_jury_source_candidates(
            candidates=[
                prepare_jury_source_candidate(
                    run_id=lease.run_id,
                    attempt=lease.attempt,
                    candidate_origin="company_evidence_db",
                    source_url="https://example.test/engineering",
                    recorded_at=fixed_time,
                    parser_route="html/main-text/v1",
                    cached_extract_text="Cached engineering platform notes.",
                    metadata={"discovery_label": "Engineering", "source_type": "official"},
                )
            ]
        )
        repository.persist_source_retrievals(
            retrievals=[
                prepare_source_retrieval(
                    run_id=lease.run_id,
                    stage_name="jury_analysis",
                    attempt=lease.attempt,
                    retrieval_origin="jury_live_acquisition",
                    source_url="https://example.test/annual-update",
                    retrieved_at=fixed_time,
                    http_status=200,
                    content_type="text/html",
                    response_content=b"<html><body>Annual update</body></html>",
                    extracted_text="Annual update on cloud modernization.",
                    parser_route="html/main-text/v1",
                    readability_status="readable",
                    retrieval_outcome="succeeded",
                )
            ]
        )


def _load_all_snapshots(repository: ScanRepository, run_id: str) -> dict[str, object]:
    return {
        "intent": repository.load_intent_input_snapshot(
            run_id=run_id, stage_name="intent", attempt=1
        ),
        "jury_analysis": repository.load_jury_input_snapshot(
            run_id=run_id, stage_name="jury_analysis", attempt=1
        ),
        "diagnosis": repository.load_diagnosis_input_snapshot(
            run_id=run_id, stage_name="diagnosis", attempt=1
        ),
        "foresight": repository.load_foresight_input_snapshot(
            run_id=run_id, stage_name="foresight", attempt=1
        ),
        "strategic_synthesis": repository.load_synthesis_input_snapshot(
            run_id=run_id, stage_name="strategic_synthesis", attempt=1
        ),
        "verification": repository.load_verification_input_snapshot(
            run_id=run_id, stage_name="verification", attempt=1
        ),
    }


def _step_rows(run_id: str) -> list[tuple[str, str, str, int]]:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stage_name, status, lease_owner, fencing_token
                FROM scan_steps
                WHERE run_id = %s
                ORDER BY created_at, stage_name
                """,
                (run_id,),
            )
            return list(cursor.fetchall())


@pytest.mark.parametrize("crash_stage", DURABLE_STAGE_ORDER)
def test_single_boundary_crash_and_reclaim_across_every_stage_in_chain(crash_stage: str):
    repo_before_crash = ScanRepository(DATABASE_URL)
    save_flag = crash_stage in ("material_ingestion", "diagnosis", "verification")
    ingress = _create_ingress_run(
        repo_before_crash,
        idempotency_key=f"boundary-{crash_stage}-{uuid4()}",
        save_to_database=save_flag,
    )
    initial_materials = repo_before_crash.load_materials(run_id=ingress.run_id)
    start = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    crash_idx = DURABLE_STAGE_ORDER.index(crash_stage)
    current_time = start

    if crash_idx > 0:
        prefix = resume_durable_chain(
            repo_before_crash,
            lease_owner=f"before-crash-{crash_stage}",
            now=current_time,
            lease_seconds=30,
            stop_after_stage=DURABLE_STAGE_ORDER[crash_idx - 1],
            on_stage_leased=_seed_stage_provenance,
        )
        assert prefix.completed_stages == DURABLE_STAGE_ORDER[:crash_idx]
        current_time = current_time + timedelta(seconds=crash_idx + 1)

    pre_crash_checkpoints = verify_completed_stage_prefix(
        repo_before_crash,
        run_id=ingress.run_id,
        completed_stages=DURABLE_STAGE_ORDER[:crash_idx],
    )

    # First process claims crash_stage, seeds provenance, binds input snapshot, and prepares output.
    stale_lease = repo_before_crash.claim_next_step(
        lease_owner=f"stale-{crash_stage}",
        lease_seconds=30,
        now=current_time,
    )
    assert stale_lease is not None
    assert stale_lease.stage_name == crash_stage
    assert stale_lease.fencing_token == 1
    _seed_stage_provenance(repo_before_crash, stale_lease)
    stale_result = prepare_leased_stage(repo_before_crash, stale_lease)

    # Fresh repository instance simulates a restarted application process after lease expiry.
    repo_after_restart = ScanRepository(DATABASE_URL)
    assert (
        repo_after_restart.claim_next_step(
            lease_owner="early-contender",
            lease_seconds=30,
            now=current_time + timedelta(seconds=15),
        )
        is None
    )

    reclaim_time = current_time + timedelta(seconds=31)
    reclaimed_lease = repo_after_restart.claim_next_step(
        lease_owner=f"reclaim-{crash_stage}",
        lease_seconds=30,
        now=reclaim_time,
    )
    assert reclaimed_lease is not None
    assert reclaimed_lease.stage_name == crash_stage
    assert reclaimed_lease.fencing_token == 2

    # Stale owner cannot commit after reclaim.
    with pytest.raises(StaleLeaseError):
        commit_prepared_stage(
            repo_before_crash,
            stale_lease,
            stale_result,
            now=reclaim_time,
        )
    assert (
        repo_after_restart.load_checkpoint(
            run_id=ingress.run_id, stage_name=crash_stage, attempt=1
        )
        is None
    )

    # Restarted owner verifies completed upstream prefix, re-prepares idempotently, and commits.
    verified_after_reclaim = verify_completed_stage_prefix(
        repo_after_restart,
        run_id=ingress.run_id,
        completed_stages=DURABLE_STAGE_ORDER[:crash_idx],
    )
    assert verified_after_reclaim == pre_crash_checkpoints

    reclaimed_result = prepare_leased_stage(repo_after_restart, reclaimed_lease)
    commit_prepared_stage(
        repo_after_restart,
        reclaimed_lease,
        reclaimed_result,
        now=reclaim_time,
    )

    if crash_stage != "verification":
        tail = resume_durable_chain(
            repo_after_restart,
            lease_owner=f"reclaim-{crash_stage}",
            now=reclaim_time + timedelta(seconds=1),
            lease_seconds=30,
            on_stage_leased=_seed_stage_provenance,
        )
        assert tail.resumed_from_stage == DURABLE_STAGE_ORDER[crash_idx + 1]
        assert tail.verified_upstream_stages == DURABLE_STAGE_ORDER[: crash_idx + 1]

    # Verify final state: no material re-upload, immutable upstream checkpoints, exact step tokens.
    final_checkpoints = verify_completed_stage_prefix(
        repo_after_restart,
        run_id=ingress.run_id,
        completed_stages=DURABLE_STAGE_ORDER,
    )
    for earlier_stage, earlier_cp in pre_crash_checkpoints.items():
        assert final_checkpoints[earlier_stage] == earlier_cp

    assert repo_after_restart.load_materials(run_id=ingress.run_id) == initial_materials
    completed_run = repo_after_restart.load_run(run_id=ingress.run_id)
    assert completed_run is not None
    assert completed_run.status == "completed"
    assert completed_run.current_stage is None
    assert completed_run.save_to_database is save_flag
    assert completed_run.final_artifact_ref is None

    steps = _step_rows(ingress.run_id)
    assert [row[0] for row in steps] == list(DURABLE_STAGE_ORDER)
    assert all(row[1] == "completed" for row in steps)
    for stage_name, _status, owner, token in steps:
        if stage_name == crash_stage:
            assert owner == f"reclaim-{crash_stage}"
            assert token == 2
        else:
            assert token == 1


def test_cascading_restarts_at_all_seven_stages_produce_identical_hashes_to_clean_run():
    shared_key = f"cascading-determinism-{uuid4()}"
    start = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)

    # 1. Clean uninterrupted reference run.
    clean_repo = ScanRepository(DATABASE_URL)
    clean_ingress = _create_ingress_run(
        clean_repo,
        idempotency_key=shared_key,
        save_to_database=False,
    )
    resume_durable_chain(
        clean_repo,
        lease_owner="clean-owner",
        now=start,
        lease_seconds=30,
        on_stage_leased=_seed_stage_provenance,
    )
    clean_checkpoints = verify_completed_stage_prefix(
        clean_repo,
        run_id=clean_ingress.run_id,
        completed_stages=DURABLE_STAGE_ORDER,
    )
    clean_snapshots = _load_all_snapshots(clean_repo, clean_ingress.run_id)
    clean_materials = clean_repo.load_materials(run_id=clean_ingress.run_id)
    clean_run = clean_repo.load_run(run_id=clean_ingress.run_id)

    # 2. Reset disposable schema and run the exact same ingress with a crash + reclaim on EVERY stage.
    _reset_schema()
    initial_repo = ScanRepository(DATABASE_URL)
    restarted_ingress = _create_ingress_run(
        initial_repo,
        idempotency_key=shared_key,
        save_to_database=False,
    )
    assert restarted_ingress.run_id == clean_ingress.run_id

    current_time = start
    committed_so_far: dict[str, object] = {}
    for idx, stage_name in enumerate(DURABLE_STAGE_ORDER):
        stale_repo = ScanRepository(DATABASE_URL)
        stale_lease = stale_repo.claim_next_step(
            lease_owner=f"stale-worker-{idx}",
            lease_seconds=30,
            now=current_time,
        )
        assert stale_lease is not None
        assert stale_lease.stage_name == stage_name
        assert stale_lease.fencing_token == 1
        _seed_stage_provenance(stale_repo, stale_lease)
        stale_proposal = prepare_leased_stage(stale_repo, stale_lease)

        current_time = current_time + timedelta(seconds=31)
        reclaim_repo = ScanRepository(DATABASE_URL)
        reclaimed_lease = reclaim_repo.claim_next_step(
            lease_owner=f"reclaim-worker-{idx}",
            lease_seconds=30,
            now=current_time,
        )
        assert reclaimed_lease is not None
        assert reclaimed_lease.stage_name == stage_name
        assert reclaimed_lease.fencing_token == 2

        with pytest.raises(StaleLeaseError):
            commit_prepared_stage(
                stale_repo,
                stale_lease,
                stale_proposal,
                now=current_time,
            )

        reloaded_prefix = verify_completed_stage_prefix(
            reclaim_repo,
            run_id=restarted_ingress.run_id,
            completed_stages=DURABLE_STAGE_ORDER[:idx],
        )
        assert reloaded_prefix == committed_so_far

        reclaimed_proposal = prepare_leased_stage(reclaim_repo, reclaimed_lease)
        committed_cp = commit_prepared_stage(
            reclaim_repo,
            reclaimed_lease,
            reclaimed_proposal,
            now=current_time,
        )
        committed_so_far[stage_name] = committed_cp
        current_time = current_time + timedelta(seconds=1)

    final_repo = ScanRepository(DATABASE_URL)
    restarted_checkpoints = verify_completed_stage_prefix(
        final_repo,
        run_id=restarted_ingress.run_id,
        completed_stages=DURABLE_STAGE_ORDER,
    )
    restarted_snapshots = _load_all_snapshots(final_repo, restarted_ingress.run_id)
    restarted_materials = final_repo.load_materials(run_id=restarted_ingress.run_id)
    restarted_run = final_repo.load_run(run_id=restarted_ingress.run_id)

    assert restarted_checkpoints == clean_checkpoints
    assert restarted_snapshots == clean_snapshots
    assert restarted_materials == clean_materials
    assert restarted_run == clean_run
    assert restarted_run is not None and restarted_run.save_to_database is False

    steps = _step_rows(restarted_ingress.run_id)
    assert all(token == 2 for _stage, _status, _owner, token in steps)
    with psycopg.connect(DATABASE_URL) as connection:
        artifact_tables = connection.execute(
            "SELECT to_regclass('public.artifacts'), to_regclass('public.scan_artifacts')"
        ).fetchone()
    assert artifact_tables == (None, None)


def test_inter_stage_restart_and_retryable_failure_resume_and_tamper_rejection():
    repo_1 = ScanRepository(DATABASE_URL)
    ingress = _create_ingress_run(
        repo_1,
        idempotency_key=f"retryable-restart-{uuid4()}",
        save_to_database=False,
    )
    now = datetime(2026, 11, 13, 11, 0, tzinfo=timezone.utc)

    # Process 1 completes material_ingestion -> intent -> jury_analysis, then exits cleanly.
    first_leg = resume_durable_chain(
        repo_1,
        lease_owner="worker-1",
        now=now,
        lease_seconds=30,
        stop_after_stage="jury_analysis",
        on_stage_leased=_seed_stage_provenance,
    )
    assert first_leg.completed_stages == ("material_ingestion", "intent", "jury_analysis")

    # Process 2 claims pending diagnosis and records a retryable operational failure.
    repo_2 = ScanRepository(DATABASE_URL)
    diagnosis_lease = repo_2.claim_next_step(
        lease_owner="worker-2",
        lease_seconds=30,
        now=now + timedelta(seconds=5),
    )
    assert diagnosis_lease is not None
    assert diagnosis_lease.stage_name == "diagnosis"
    repo_2.fail_step(
        diagnosis_lease,
        retryable=True,
        reason="transient provider timeout",
        now=now + timedelta(seconds=6),
    )

    # Tamper upstream jury_analysis checkpoint JSONB: Process 3 must refuse to resume.
    original_jury = repo_2.load_checkpoint(
        run_id=ingress.run_id, stage_name="jury_analysis", attempt=1
    )
    assert original_jury is not None
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET snapshot = jsonb_set(snapshot, '{jury_result,juries,0,confidence_score}', '99'::jsonb)
            WHERE run_id = %s AND stage_name = 'jury_analysis' AND attempt = 1
            """,
            (ingress.run_id,),
        )

    repo_3 = ScanRepository(DATABASE_URL)
    with pytest.raises(CheckpointIntegrityError):
        resume_durable_chain(
            repo_3,
            lease_owner="worker-3-blocked",
            now=now + timedelta(seconds=7),
            lease_seconds=30,
        )
    # Foresight was never queued while jury_analysis was tampered.
    assert [row[0] for row in _step_rows(ingress.run_id)] == [
        "material_ingestion",
        "intent",
        "jury_analysis",
        "diagnosis",
    ]

    # Restore untampered jury_analysis snapshot and expire worker-3-blocked's lease; worker-4 completes.
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE scan_checkpoints
            SET snapshot = %s::jsonb
            WHERE run_id = %s AND stage_name = 'jury_analysis' AND attempt = 1
            """,
            (json.dumps(original_jury.snapshot), ingress.run_id),
        )

    repo_4 = ScanRepository(DATABASE_URL)
    resumed = resume_durable_chain(
        repo_4,
        lease_owner="worker-4",
        now=now + timedelta(seconds=40),
        lease_seconds=30,
    )
    assert resumed.resumed_from_stage == "diagnosis"
    assert resumed.completed_stages == (
        "diagnosis",
        "foresight",
        "strategic_synthesis",
        "verification",
    )
    final_run = repo_4.load_run(run_id=ingress.run_id)
    assert final_run is not None
    assert final_run.status == "completed"
    assert final_run.save_to_database is False


def test_real_os_sigkill_process_loss_mid_chain_reclaims_and_completes():
    repo = ScanRepository(DATABASE_URL)
    ingress = _create_ingress_run(
        repo,
        idempotency_key=f"sigkill-restart-{uuid4()}",
        save_to_database=False,
    )
    child_script = """
import os, sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, sys.argv[1])
from scan_durable_loop import prepare_leased_stage, resume_durable_chain
from scan_supervisor import ScanRepository

repo = ScanRepository(os.environ["SCANNER_TEST_DATABASE_URL"])
now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
resume_durable_chain(
    repo,
    lease_owner="child-before-sigkill",
    now=now,
    lease_seconds=30,
    stop_after_stage="jury_analysis",
)
lease = repo.claim_next_step(
    lease_owner="child-before-sigkill",
    lease_seconds=30,
    now=now + timedelta(seconds=5),
)
assert lease is not None and lease.stage_name == "diagnosis"
prepare_leased_stage(repo, lease)
sys.stdout.write("READY_FOR_SIGKILL\\n")
sys.stdout.flush()
time.sleep(60)
"""
    child_env = {
        **os.environ,
        "SCANNER_TEST_DATABASE_URL": DATABASE_URL,
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", child_script, str(_BACKEND)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    try:
        assert proc.stdout is not None
        ready_line = proc.stdout.readline().strip()
        assert ready_line == "READY_FOR_SIGKILL"
        os.kill(proc.pid, signal.SIGKILL)
        exit_code = proc.wait(timeout=5)
        assert exit_code != 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # Diagnosis step is still leased by the dead child process and has 0 checkpoints.
    recovery_repo = ScanRepository(DATABASE_URL)
    assert (
        recovery_repo.load_checkpoint(
            run_id=ingress.run_id, stage_name="diagnosis", attempt=1
        )
        is None
    )

    # After lease expiry, recovery process reclaims diagnosis, verifies upstream checkpoints, and finishes.
    resumed = resume_durable_chain(
        recovery_repo,
        lease_owner="parent-after-sigkill",
        now=datetime(2026, 11, 13, 12, 0, 36, tzinfo=timezone.utc),
        lease_seconds=30,
    )
    assert resumed.resumed_from_stage == "diagnosis"
    assert resumed.verified_upstream_stages == (
        "material_ingestion",
        "intent",
        "jury_analysis",
    )
    assert resumed.completed_stages == (
        "diagnosis",
        "foresight",
        "strategic_synthesis",
        "verification",
    )

    completed_run = recovery_repo.load_run(run_id=ingress.run_id)
    assert completed_run is not None
    assert completed_run.status == "completed"
    assert completed_run.current_stage is None
    assert completed_run.save_to_database is False
    steps = dict(
        (stage_name, (status, owner, token))
        for stage_name, status, owner, token in _step_rows(ingress.run_id)
    )
    assert steps["diagnosis"] == ("completed", "parent-after-sigkill", 2)
    assert steps["verification"] == ("completed", "parent-after-sigkill", 1)
