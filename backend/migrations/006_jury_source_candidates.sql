-- Scanner Reliability v2 — Phase 3 Jury-stage candidate provenance.
--
-- A Jury source candidate is acquisition/discovery lineage for the
-- jury_analysis step. It is not Company Evidence, Foresight eligibility,
-- claim support, or a publication artifact. Cached extract text is optional
-- and hash-verified; raw response bytes are never stored.

BEGIN;

CREATE TABLE IF NOT EXISTS jury_source_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL CHECK (stage_name = 'jury_analysis'),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    candidate_origin TEXT NOT NULL CHECK (
        candidate_origin IN (
            'company_evidence_db',
            'previous_scan_source_cache',
            'jury_live_acquisition_seed'
        )
    ),
    source_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    parser_route TEXT NOT NULL,
    cached_extract_sha256 TEXT NOT NULL CHECK (char_length(cached_extract_sha256) = 64),
    cached_extract_text TEXT NOT NULL,
    extraction_warnings JSONB NOT NULL DEFAULT '{"warnings":[]}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (run_id, stage_name, attempt)
        REFERENCES scan_steps(run_id, stage_name, attempt)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS jury_source_candidates_step_recorded_idx
    ON jury_source_candidates (run_id, stage_name, attempt, recorded_at, candidate_id);

CREATE INDEX IF NOT EXISTS jury_source_candidates_canonical_url_idx
    ON jury_source_candidates (canonical_url, recorded_at);

COMMIT;
