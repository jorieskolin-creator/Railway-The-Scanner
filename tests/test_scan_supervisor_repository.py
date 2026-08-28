"""Phase 2 contract tests for PostgreSQL Scan Supervisor primitives."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest


_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_supervisor import (  # noqa: E402
    ScanRepository,
    ScanSupervisorConfigurationError,
    ScanSupervisorSettings,
    StaleLeaseError,
    StepLease,
)


class RecordingCursor:
    def __init__(self, *, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, query, parameters=None):
        self.executed.append((query, parameters))

    def fetchone(self):
        return self.row


class RecordingConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _repo(cursor):
    connection = RecordingConnection(cursor)
    return (
        ScanRepository(
            "postgresql://scanner:test@localhost/scanner",
            connection_factory=lambda _dsn: connection,
        ),
        connection,
    )


def _lease(now):
    return StepLease(
        run_id="RUN-001",
        stage_name="material_ingestion",
        attempt=1,
        lease_owner="supervisor-a",
        fencing_token=7,
        lease_expires_at=now + timedelta(seconds=60),
    )


def test_supervisor_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SCAN_SUPERVISOR_ENABLED", raising=False)
    monkeypatch.delenv("SCANNER_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SCAN_SUPERVISOR_SCHEMA_READY", raising=False)

    settings = ScanSupervisorSettings.from_env()

    assert settings.enabled is False
    assert settings.schema_ready is False
    assert settings.database_url == ""


def test_enabled_supervisor_requires_database_url(monkeypatch):
    monkeypatch.setenv("SCAN_SUPERVISOR_ENABLED", "true")
    monkeypatch.delenv("SCANNER_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ScanSupervisorConfigurationError):
        ScanSupervisorSettings.from_env()


def test_claim_uses_transaction_lock_expiry_reclaim_and_fencing_token():
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    cursor = RecordingCursor(
        row={
            "run_id": "RUN-001",
            "stage_name": "material_ingestion",
            "attempt": 1,
            "lease_owner": "supervisor-a",
            "fencing_token": 8,
            "lease_expires_at": now + timedelta(seconds=90),
        }
    )
    repo, connection = _repo(cursor)

    lease = repo.claim_next_step(
        lease_owner="supervisor-a",
        lease_seconds=90,
        now=now,
    )

    sql = cursor.executed[0][0]
    normalized = " ".join(sql.lower().split())
    assert "pg_try_advisory_xact_lock" in normalized
    assert "for update skip locked" in normalized
    assert "lease_expires_at <= %s" in normalized
    assert "fencing_token = s.fencing_token + 1" in normalized
    assert lease and lease.fencing_token == 8
    assert connection.committed is True
    assert connection.closed is True


def test_heartbeat_is_fenced_by_owner_token_and_unexpired_lease():
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    cursor = RecordingCursor(rowcount=1)
    repo, _connection = _repo(cursor)

    assert repo.heartbeat(_lease(now), lease_seconds=90, now=now) is True

    sql, parameters = cursor.executed[0]
    normalized = " ".join(sql.lower().split())
    assert "lease_owner = %s" in normalized
    assert "fencing_token = %s" in normalized
    assert "lease_expires_at > %s" in normalized
    assert parameters[-3:] == ("supervisor-a", 7, now)


def test_stale_attempt_cannot_commit_checkpoint():
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    cursor = RecordingCursor(rowcount=0)
    repo, connection = _repo(cursor)

    with pytest.raises(StaleLeaseError):
        repo.complete_step(
            _lease(now),
            output_hash="a" * 64,
            checkpoint_ref="checkpoint://RUN-001/materials",
            gate_result={"gate": "material/v1", "status": "passed"},
            next_stage="intent",
            now=now,
        )

    sql, parameters = cursor.executed[0]
    normalized = " ".join(sql.lower().split())
    assert "lease_owner = %s" in normalized
    assert "fencing_token = %s" in normalized
    assert "lease_expires_at > %s" in normalized
    assert parameters[-3:] == ("supervisor-a", 7, now)
    assert connection.rolled_back is True


def test_execution_migration_keeps_operational_state_separate_from_evidence():
    migration_path = os.path.join(_BACKEND, "migrations", "001_scan_execution_state.sql")
    with open(migration_path, encoding="utf-8") as migration_file:
        migration = migration_file.read().lower()

    assert "create table if not exists scan_runs" in migration
    assert "create table if not exists scan_steps" in migration
    assert "create table if not exists scan_events" in migration
    assert "scan_runs_single_running_idx" in migration
    assert "fencing_token bigint not null" in migration
    assert "evidence_records" not in migration
    assert "foresight_signals" not in migration
