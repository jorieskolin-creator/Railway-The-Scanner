-- Scanner Reliability v2 — Phase 3 immutable checkpoint foundation.
--
-- Checkpoints contain operational stage lineage only: hashes, gate verdicts,
-- and stable material references.  They are neither Company Evidence nor
-- Foresight content, and contain no raw uploaded bytes or extracted text.

BEGIN;

CREATE TABLE IF NOT EXISTS scan_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    contract_version TEXT NOT NULL,
    input_hash TEXT NOT NULL CHECK (char_length(input_hash) = 64),
    output_hash TEXT NOT NULL CHECK (char_length(output_hash) = 64),
    gate_result JSONB NOT NULL,
    snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, stage_name, attempt),
    FOREIGN KEY (run_id, stage_name, attempt)
        REFERENCES scan_steps(run_id, stage_name, attempt)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS scan_checkpoints_run_stage_created_idx
    ON scan_checkpoints (run_id, stage_name, created_at, checkpoint_id);

COMMIT;
