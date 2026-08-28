-- Scanner Reliability v2 — Phase 3 material durability foundation.
--
-- Material is submitted/extracted input only.  It is not evidence, does not
-- establish source truth, and does not share tables with Foresight or Company
-- Summary publication.  Raw uploaded bytes are intentionally not retained.

BEGIN;

CREATE TABLE IF NOT EXISTS scan_materials (
    material_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES scan_runs(run_id) ON DELETE CASCADE,
    origin TEXT NOT NULL CHECK (
        origin IN ('submitted_report_text', 'uploaded_file', 'source_retrieval')
    ),
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    content_sha256 TEXT NOT NULL CHECK (char_length(content_sha256) = 64),
    extracted_text_sha256 TEXT NOT NULL CHECK (char_length(extracted_text_sha256) = 64),
    parser_version TEXT NOT NULL,
    extraction_status TEXT NOT NULL CHECK (
        extraction_status IN ('extracted', 'empty', 'failed')
    ),
    extraction_warnings JSONB NOT NULL DEFAULT '{"warnings":[]}'::jsonb,
    allowed_use_classification TEXT NOT NULL,
    extracted_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS scan_materials_run_created_idx
    ON scan_materials (run_id, created_at, material_id);

CREATE INDEX IF NOT EXISTS scan_materials_content_hash_idx
    ON scan_materials (content_sha256);

COMMIT;
