CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_config (
    source_type TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS ingest_queries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    source_type TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id UUID PRIMARY KEY,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    rows_fetched INTEGER,
    rows_upserted INTEGER,
    watermark_before TIMESTAMPTZ,
    watermark_after TIMESTAMPTZ,
    error_message TEXT,
    source_available BOOLEAN,
    payload_warning TEXT
);

CREATE TABLE IF NOT EXISTS raw_documents (
    id UUID PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    platform TEXT,
    raw_text TEXT,
    raw_title TEXT,
    star_rating INTEGER,
    parent_context JSONB,
    author_hash TEXT,
    payload_uri TEXT,
    myntra_relevance TEXT,
    reject_reason TEXT,
    content_hash TEXT,
    ingest_run_id UUID REFERENCES ingest_runs (id),
    date_anomaly BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (source_type, source_id)
);

CREATE INDEX IF NOT EXISTS raw_documents_source_published_idx
    ON raw_documents (source_type, published_at DESC);

CREATE INDEX IF NOT EXISTS raw_documents_ingest_run_idx
    ON raw_documents (ingest_run_id);

CREATE TABLE IF NOT EXISTS normalize_runs (
    id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    since_ingest_run_id UUID REFERENCES ingest_runs (id),
    rows_accepted INTEGER,
    rows_rejected INTEGER,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS normalized_documents (
    id UUID PRIMARY KEY,
    raw_id UUID NOT NULL UNIQUE REFERENCES raw_documents (id),
    text_original TEXT NOT NULL,
    text_en TEXT,
    language TEXT NOT NULL,
    product_category TEXT NOT NULL DEFAULT 'unknown',
    gender_segment TEXT NOT NULL DEFAULT 'unknown',
    price_tier TEXT NOT NULL DEFAULT 'unknown',
    platform_used TEXT NOT NULL DEFAULT 'unknown',
    occasion TEXT NOT NULL DEFAULT 'unknown',
    star_rating INTEGER,
    review_date TIMESTAMPTZ,
    quality_score REAL,
    content_hash TEXT,
    duplicate_of UUID REFERENCES normalized_documents (id),
    eligible BOOLEAN NOT NULL DEFAULT TRUE,
    pii_scrubbed_at TIMESTAMPTZ NOT NULL,
    normalize_run_id UUID REFERENCES normalize_runs (id),
    intent_mode TEXT
);

CREATE INDEX IF NOT EXISTS normalized_documents_content_hash_idx
    ON normalized_documents (content_hash);

CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES normalized_documents (id),
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER,
    embedding vector(1024),
    embedding_model TEXT
);

-- vector(1024) is reserved for local BGE-M3 (Architecture §5.1). Do not mix checkpoints.

CREATE OR REPLACE VIEW source_status AS
SELECT
    sc.source_type,
    CASE
        WHEN sc.enabled IS FALSE THEN 'unavailable'
        WHEN lr.status = 'failed' THEN 'failed'
        WHEN lr.status = 'skipped_disabled' THEN 'unavailable'
        WHEN lr.status = 'success' THEN 'live'
        ELSE 'unavailable'
    END AS status,
    sc.enabled,
    sc.notes,
    lr.id AS last_run_id,
    lr.status AS last_run_status,
    lr.finished_at AS last_run_finished_at,
    lr.rows_fetched AS last_rows_fetched,
    lr.source_available AS last_source_available
FROM source_config sc
LEFT JOIN LATERAL (
    SELECT *
    FROM ingest_runs ir
    WHERE ir.source_type = sc.source_type
    ORDER BY ir.started_at DESC
    LIMIT 1
) lr ON TRUE;
