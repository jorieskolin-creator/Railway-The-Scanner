-- Scanner Reliability v2 — Phase 3 immutable Intent input manifest.
--
-- This manifest binds an Intent attempt to exact hash-verified material and
-- retrieval references. It is operational lineage, not Company Evidence,
-- Foresight content, a claim-support decision, or a published artifact. It
-- intentionally contains no extracted text or raw response bytes.

BEGIN;

CREATE TABLE IF NOT EXISTS intent_input_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL CHECK (stage_name = 'intent'),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    contract_version TEXT NOT NULL,
    input_hash TEXT NOT NULL CHECK (char_length(input_hash) = 64),
    manifest JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, stage_name, attempt),
    FOREIGN KEY (run_id, stage_name, attempt)
        REFERENCES scan_steps(run_id, stage_name, attempt)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS intent_input_snapshots_run_stage_created_idx
    ON intent_input_snapshots (run_id, stage_name, created_at, snapshot_id);

COMMIT;
