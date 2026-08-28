-- Scanner Reliability v2 — Phase 2 execution-state foundation.
--
-- This migration creates operational state only.  It does not store evidence,
-- Foresight content, material extracts, or published artifacts.  The
-- Supervisor remains disabled until a deployment explicitly enables it.

BEGIN;

CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    company_url TEXT,
    cognitive_contract_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'blocked', 'cancelled')
    ),
    current_stage TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    final_artifact_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- One run may execute at a time in the single in-process Supervisor model.
CREATE UNIQUE INDEX IF NOT EXISTS scan_runs_single_running_idx
    ON scan_runs ((1))
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS scan_steps (
    run_id TEXT NOT NULL REFERENCES scan_runs(run_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    stage_order INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending', 'leased', 'running', 'completed',
            'retryable_failed', 'terminal_failed', 'blocked'
        )
    ),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    input_hash TEXT,
    output_hash TEXT,
    checkpoint_ref TEXT,
    gate_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    failure_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (run_id, stage_name, attempt)
);

-- A run cannot have two live stages.  An expired lease is deliberately still
-- live at the schema level until the repository reclaims it with a new token.
CREATE UNIQUE INDEX IF NOT EXISTS scan_steps_one_active_per_run_idx
    ON scan_steps (run_id)
    WHERE status IN ('leased', 'running');

CREATE INDEX IF NOT EXISTS scan_steps_claimable_idx
    ON scan_steps (status, lease_expires_at, stage_order, created_at);

CREATE TABLE IF NOT EXISTS scan_events (
    event_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES scan_runs(run_id) ON DELETE CASCADE,
    stage_name TEXT,
    attempt INTEGER,
    event_type TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scan_events_run_created_idx
    ON scan_events (run_id, created_at, event_id);

COMMIT;
