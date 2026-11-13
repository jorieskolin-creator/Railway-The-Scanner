-- Scanner Reliability v2 — Card 3K-integrity durable save_to_database flag.
--
-- Persists the operator's save_to_database intent on scan_runs as operational
-- state only.  This column does not store domain artifacts or replace Vercel
-- Blob; it preserves whether a resumed run may publish shared company records.

BEGIN;

ALTER TABLE scan_runs
    ADD COLUMN IF NOT EXISTS save_to_database BOOLEAN NOT NULL DEFAULT TRUE;

COMMIT;
