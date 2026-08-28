-- Scanner Reliability v2 — Phase 3 durable source-retrieval foundation.
--
-- A retrieval record is acquisition provenance, not Company Evidence or
-- Foresight.  It stores extracted text and hashes for resumability, but never
-- raw response bytes and never a claim-support verdict.

BEGIN;

CREATE TABLE IF NOT EXISTS source_retrievals (
    retrieval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    retrieval_origin TEXT NOT NULL,
    source_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    http_status INTEGER CHECK (http_status BETWEEN 100 AND 599),
    content_type TEXT NOT NULL,
    response_sha256 TEXT NOT NULL CHECK (char_length(response_sha256) = 64),
    extracted_text_sha256 TEXT NOT NULL CHECK (char_length(extracted_text_sha256) = 64),
    parser_route TEXT NOT NULL,
    readability_status TEXT NOT NULL CHECK (
        readability_status IN ('readable', 'unreadable', 'unknown')
    ),
    retrieval_outcome TEXT NOT NULL CHECK (
        retrieval_outcome IN ('succeeded', 'not_found', 'blocked', 'failed')
    ),
    extraction_warnings JSONB NOT NULL DEFAULT '{"warnings":[]}'::jsonb,
    response_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    extracted_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (run_id, stage_name, attempt)
        REFERENCES scan_steps(run_id, stage_name, attempt)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS source_retrievals_step_retrieved_idx
    ON source_retrievals (run_id, stage_name, attempt, retrieved_at, retrieval_id);

CREATE INDEX IF NOT EXISTS source_retrievals_canonical_url_idx
    ON source_retrievals (canonical_url, retrieved_at);

COMMIT;
