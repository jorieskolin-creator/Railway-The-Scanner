-- Scanner Reliability v2 — Phase 4 deterministic gate-result foundation.
--
-- Gate results record operational validation, integrity, and policy verdicts
-- for stage ingress and egress boundaries. They are neither Company Evidence
-- nor Foresight Knowledge Base content, and intentionally contain no raw
-- uploaded bytes, extracted source text, KB signal payloads, or publication
-- authority.

BEGIN;

CREATE TABLE IF NOT EXISTS gate_results (
    gate_result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    gate_name TEXT NOT NULL,
    gate_version TEXT NOT NULL,
    envelope_contract TEXT NOT NULL,
    input_hash TEXT CHECK (input_hash IS NULL OR input_hash ~ '^[a-fA-F0-9]{64}$'),
    output_hash TEXT CHECK (output_hash IS NULL OR output_hash ~ '^[a-fA-F0-9]{64}$'),
    input_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL CHECK (
        status IN (
            'passed',
            'failed',
            'blocked',
            'insufficient_evidence',
            'validation_failed',
            'approval_required'
        )
    ),
    blocking_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    envelope JSONB NOT NULL,
    record_sha256 TEXT NOT NULL CHECK (char_length(record_sha256) = 64),
    checked_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, stage_name, attempt, gate_name),
    FOREIGN KEY (run_id, stage_name, attempt)
        REFERENCES scan_steps(run_id, stage_name, attempt)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS gate_results_run_stage_checked_idx
    ON gate_results (run_id, stage_name, attempt, checked_at, gate_result_id);

COMMIT;
