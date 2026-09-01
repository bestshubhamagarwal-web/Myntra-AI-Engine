-- Phase 2: Groq extractions + BGE chunk metadata (Architecture §8.1–8.2).
-- Failed / pending extractions stay auditable and are excluded from theme metrics.

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS embedding_revision TEXT,
    ADD COLUMN IF NOT EXISTS embedding_dim INTEGER,
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS source_type TEXT,
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS product_category TEXT,
    ADD COLUMN IF NOT EXISTS intent_tag TEXT,
    ADD COLUMN IF NOT EXISTS intent_mode TEXT,
    ADD COLUMN IF NOT EXISTS friction_tags JSONB,
    ADD COLUMN IF NOT EXISTS sentiment TEXT,
    ADD COLUMN IF NOT EXISTS maps_to_questions JSONB,
    ADD COLUMN IF NOT EXISTS extraction_status TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS chunks_document_ordinal_uidx
    ON chunks (document_id, ordinal);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks (document_id);

CREATE INDEX IF NOT EXISTS chunks_friction_gin
    ON chunks USING gin (friction_tags jsonb_path_ops);

CREATE INDEX IF NOT EXISTS chunks_intent_mode_idx ON chunks (intent_mode);

CREATE TABLE IF NOT EXISTS extractions (
    document_id UUID PRIMARY KEY REFERENCES normalized_documents (id),
    content_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    groq_model TEXT,
    extraction_status TEXT NOT NULL,
    intent_tag TEXT,
    intent_mode TEXT,
    friction_tags JSONB,
    residual_uncertainties JSONB,
    comparison_behavior TEXT,
    off_platform_info_seeking JSONB,
    entities JSONB,
    sentiment_primary TEXT,
    sentiment_severity REAL,
    verbatim_quotes JSONB,
    maps_to_questions JSONB,
    extraction_confidence REAL,
    raw_response TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    extracted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metrics_eligible BOOLEAN GENERATED ALWAYS AS (extraction_status = 'ok') STORED
);

CREATE INDEX IF NOT EXISTS extractions_status_idx ON extractions (extraction_status);
CREATE INDEX IF NOT EXISTS extractions_intent_mode_idx ON extractions (intent_mode);
CREATE INDEX IF NOT EXISTS extractions_intent_tag_idx ON extractions (intent_tag);
CREATE INDEX IF NOT EXISTS extractions_friction_gin
    ON extractions USING gin (friction_tags jsonb_path_ops);
CREATE INDEX IF NOT EXISTS extractions_maps_gin
    ON extractions USING gin (maps_to_questions jsonb_path_ops);
CREATE INDEX IF NOT EXISTS extractions_metrics_eligible_idx
    ON extractions (metrics_eligible);

-- Theme metrics / clustering MUST use this view (or metrics_eligible = TRUE).
-- Failed Groq JSON remains on extractions + normalized_documents for evidence,
-- but is not a SoV / mention_count member.
CREATE OR REPLACE VIEW extraction_metrics_eligible AS
SELECT e.*
FROM extractions e
JOIN normalized_documents n ON n.id = e.document_id
WHERE e.extraction_status = 'ok'
  AND e.metrics_eligible IS TRUE
  AND n.eligible IS TRUE;

CREATE TABLE IF NOT EXISTS extract_runs (
    id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    prompt_version TEXT,
    groq_model TEXT,
    rows_ok INTEGER,
    rows_failed INTEGER,
    rows_skipped INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    resume_after_document_id UUID,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS embed_runs (
    id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    embedding_model TEXT,
    embedding_revision TEXT,
    embedding_dim INTEGER,
    rows_encoded INTEGER,
    rows_skipped INTEGER,
    error_message TEXT
);
