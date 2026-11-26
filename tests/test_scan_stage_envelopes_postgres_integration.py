"""Card 4A contracts on an isolated, loopback-only PostgreSQL schema."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import sql

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND))

from scan_durable_loop import DURABLE_STAGE_ORDER, _bind_stage_inputs, _executor_for, drive_durable_chain
from scan_ingress import prepare_scan_ingress
from scan_stage_envelopes import StageBindings, run_gated_stage_step
from scan_supervisor import GateResultIntegrityError, ScanRepository, StaleLeaseError

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)


@pytest.fixture
def repository():
    dsn = os.environ.get("SCANNER_TEST_DATABASE_URL", "")
    url = urlparse(dsn)
    if not (os.environ.get("SCANNER_ALLOW_LOOPBACK_TEST_DATABASE") == "true"
            and url.scheme in {"postgres", "postgresql"} and url.hostname == "127.0.0.1"
            and url.port == 5432 and url.path == "/scanner_test" and not url.query):
        pytest.skip("requires the explicitly configured disposable loopback database")
    schema = f"card4a_{uuid4().hex}"

    def connect(dsn):
        return psycopg.connect(dsn, hostaddr="127.0.0.1", options=f"-c search_path={schema}", row_factory=psycopg.rows.dict_row)

    with psycopg.connect(dsn, hostaddr="127.0.0.1", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with connect(dsn) as connection:
                connection.autocommit = True
                for migration in sorted((_BACKEND / "migrations").glob("*.sql")):
                    connection.execute(migration.read_text(encoding="utf-8"))
            yield ScanRepository(dsn, connection_factory=connect)
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _create(repository):
    ingress = prepare_scan_ingress(
        company_name="Card 4A Fixture", company_url="https://example.test",
        report_text="Submitted context", uploads=[], save_to_database=False,
        idempotency_key="gate-fixture",
    )
    repository.create_run_with_materials(
        run_id=ingress.run_id, company_name=ingress.company_name, company_url=ingress.company_url,
        idempotency_key=ingress.idempotency_key, request_hash=ingress.request_hash,
        cognitive_contract_version=ingress.cognitive_contract_version, initial_stage=ingress.initial_stage,
        materials=list(ingress.materials), save_to_database=False,
    )
    return ingress.run_id


def _claim(repository, *, now=NOW, owner="owner"):
    lease = repository.claim_next_step(lease_owner=owner, lease_seconds=30, now=now)
    assert lease is not None
    return lease


def _stage(repository, target):
    for stage in DURABLE_STAGE_ORDER:
        lease = _claim(repository)
        assert lease.stage_name == stage
        if stage == target:
            return lease
        _bind_stage_inputs(repository, lease)
        assert run_gated_stage_step(repository, lease, _executor_for(repository, lease), now=NOW).checkpoint
    raise AssertionError("Unexpected target stage")


def test_full_chain_atomically_persists_and_reloads_both_gates(repository):
    run_id = _create(repository)
    assert drive_durable_chain(repository, lease_owner="owner", now=NOW, persist_gates=True).stages == DURABLE_STAGE_ORDER
    assert repository.load_run(run_id=run_id).status == "completed"
    records = repository.load_gate_results(run_id=run_id)
    assert len(records) == 14
    for stage in DURABLE_STAGE_ORDER:
        checkpoint = repository.load_checkpoint(run_id=run_id, stage_name=stage, attempt=1)
        gates = {r.gate_name: r for r in records if r.stage_name == stage}
        assert set(gates) == {"stage_ingress", "stage_egress"}
        assert all(r.status == "passed" and r.input_hash == checkpoint.input_hash for r in gates.values())
        assert gates["stage_egress"].output_hash == checkpoint.output_hash
    with repository._transaction() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE gate_results SET envelope = envelope || '{\"source_text\":\"tampered\"}'::jsonb WHERE stage_name = 'verification'")
    with pytest.raises(GateResultIntegrityError):
        repository.load_gate_results(run_id=run_id)


@pytest.mark.parametrize("fault,stage,status,code", [
    ("missing", "intent", "blocked", "missing_durable_input"),
    ("tampered", "intent", "blocked", "durable_input_integrity_failed"),
    ("upstream_tamper", "diagnosis", "blocked", "durable_input_integrity_failed"),
    ("upstream_gate_tamper", "diagnosis", "blocked", "durable_input_integrity_failed"),
    ("missing_upstream_gate", "diagnosis", "blocked", "durable_input_integrity_failed"),
    ("policy_upgrade", "diagnosis", "blocked", "stale_version_binding"),
    ("kb_upgrade", "verification", "blocked", "stale_version_binding"),
    ("malformed", "intent", "validation_failed", "malformed_intent_result"),
    ("contradiction", "intent", "validation_failed", "contradictory_stage_result"),
    ("output_tamper", "intent", "validation_failed", "invalid_checkpoint"),
])
def test_bad_inputs_and_outputs_block_without_advancing(repository, monkeypatch, fault, stage, status, code):
    run_id = _create(repository)
    lease = _stage(repository, stage)
    if fault != "missing":
        _bind_stage_inputs(repository, lease)
    tamper_sql = {
        "tampered": "UPDATE scan_materials SET extracted_text = 'tampered'",
        "upstream_tamper": "UPDATE scan_checkpoints SET snapshot = snapshot || '{\"tampered\":true}'::jsonb WHERE stage_name = 'jury_analysis'",
        "upstream_gate_tamper": "UPDATE gate_results SET status = 'failed' WHERE stage_name = 'jury_analysis'",
        "missing_upstream_gate": "DELETE FROM gate_results WHERE stage_name = 'jury_analysis' AND gate_name = 'stage_egress'",
    }
    if fault in tamper_sql:
        with repository._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(tamper_sql[fault])
    if fault == "malformed":
        monkeypatch.setattr("scan_intent_adapter.analyze_intent", lambda text: {})

    calls = []

    class Executor:
        def execute(self, lease):
            calls.append(lease.stage_name)
            proposal = _executor_for(repository, lease).execute(lease)
            if fault == "contradiction":
                proposal = replace(proposal, next_stage="verification")
            if fault == "output_tamper":
                proposal = replace(proposal, checkpoint=replace(proposal.checkpoint, output_hash="a" * 64))
            return proposal

    bindings = StageBindings()
    if fault == "policy_upgrade":
        bindings = replace(bindings, model_policy_version="new-model-policy")
    if fault == "kb_upgrade":
        bindings = replace(bindings, kb_registry_hash="b" * 64)
    outcome = run_gated_stage_step(repository, lease, Executor(), bindings=bindings, now=NOW)
    assert calls == ([stage] if status == "validation_failed" else [])
    assert outcome.checkpoint is None
    assert outcome.envelope.gate_results[0].status == status
    assert outcome.envelope.gate_results[0].reasons == (code,)
    assert repository.load_run(run_id=run_id).status == "blocked"
    assert repository.load_checkpoint(run_id=run_id, stage_name=stage, attempt=1) is None
    gates = repository.load_gate_results(run_id=run_id, stage_name=stage, attempt=1)
    assert len(gates) == (2 if calls else 1)
    assert {g.gate_name: g.status for g in gates}[outcome.envelope.gate_results[0].gate] == status
    with repository._transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT stage_name, status FROM scan_steps")
        assert {row["stage_name"]: row["status"] for row in cursor.fetchall()} == {
            **dict.fromkeys(DURABLE_STAGE_ORDER[:DURABLE_STAGE_ORDER.index(stage)], "completed"), stage: "blocked",
        }
        cursor.execute("SELECT count(*) AS n FROM scan_events WHERE event_type = 'step_blocked'")
        assert cursor.fetchone()["n"] == 1
    assert repository.claim_next_step(lease_owner="other", lease_seconds=30, now=NOW) is None


@pytest.mark.parametrize("blocked", [False, True])
def test_stale_owner_leaves_no_checkpoint_or_gate_rows(repository, blocked):
    run_id = _create(repository)
    lease = _stage(repository, "intent" if blocked else "material_ingestion")
    later = NOW + timedelta(seconds=31)
    fresh = _claim(repository, now=later, owner="replacement")
    with pytest.raises(StaleLeaseError):
        run_gated_stage_step(repository, lease, _executor_for(repository, lease), now=later)
    assert repository.load_gate_results(run_id=run_id, stage_name=lease.stage_name) == []
    assert repository.load_checkpoint(run_id=run_id, stage_name=lease.stage_name, attempt=1) is None
    outcome = run_gated_stage_step(repository, fresh, _executor_for(repository, fresh), now=later)
    assert (outcome.checkpoint is None) == blocked
    with pytest.raises(StaleLeaseError):
        run_gated_stage_step(repository, fresh, _executor_for(repository, fresh), now=later)
    assert len(repository.load_gate_results(run_id=run_id, stage_name=lease.stage_name)) == (1 if blocked else 2)


def test_second_gate_insert_failure_rolls_back_the_entire_completion(repository):
    run_id = _create(repository)
    lease = _claim(repository)
    with repository._transaction() as connection, connection.cursor() as cursor:
        cursor.execute("ALTER TABLE gate_results ADD CHECK (gate_name <> 'stage_egress')")
    with pytest.raises(psycopg.errors.CheckViolation):
        run_gated_stage_step(repository, lease, _executor_for(repository, lease), now=NOW)
    assert repository.load_gate_results(run_id=run_id) == []
    assert repository.load_checkpoint(run_id=run_id, stage_name=lease.stage_name, attempt=1) is None
    assert repository.load_run(run_id=run_id).current_stage == lease.stage_name
    with repository._transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT stage_name, status FROM scan_steps")
        assert cursor.fetchall() == [{"stage_name": lease.stage_name, "status": "leased"}]
        cursor.execute("SELECT count(*) AS n FROM scan_events WHERE event_type = 'step_completed'")
        assert cursor.fetchone()["n"] == 0
