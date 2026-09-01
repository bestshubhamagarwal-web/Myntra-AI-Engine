-- Phase 5: Query API artifacts — n-grams, chat, weekly reports (Architecture §9–13).
-- Metrics stay on theme_metrics / SQL functions from 005_phase4.sql. Do not re-derive SoV here.

CREATE TABLE IF NOT EXISTS ngrams (
    id UUID PRIMARY KEY,
    cluster_run_id UUID REFERENCES cluster_runs (id),
    gram TEXT NOT NULL,
    n INTEGER NOT NULL,
    theme_id UUID REFERENCES themes (id),
    category TEXT,
    sentiment TEXT,
    count INTEGER NOT NULL DEFAULT 0,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ngrams_run_idx ON ngrams (cluster_run_id);
CREATE INDEX IF NOT EXISTS ngrams_theme_idx ON ngrams (theme_id);
CREATE INDEX IF NOT EXISTS ngrams_category_idx ON ngrams (category);
CREATE INDEX IF NOT EXISTS ngrams_gram_idx ON ngrams (gram);
CREATE UNIQUE INDEX IF NOT EXISTS ngrams_run_key_uidx
    ON ngrams (
        cluster_run_id,
        n,
        gram,
        COALESCE(theme_id, '00000000-0000-0000-0000-000000000000'::uuid),
        COALESCE(category, ''),
        COALESCE(sentiment, '')
    );

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    groq_model TEXT,
    bge_model TEXT,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions (id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    citations JSONB,
    metrics_used JSONB,
    tools_used JSONB,
    confidence_band TEXT,
    status TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages (session_id, created_at);

CREATE TABLE IF NOT EXISTS report_artifacts (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    cluster_run_id UUID REFERENCES cluster_runs (id),
    previous_cluster_run_id UUID REFERENCES cluster_runs (id),
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    path TEXT,
    header JSONB NOT NULL DEFAULT '{}'::jsonb,
    diff JSONB NOT NULL DEFAULT '{}'::jsonb,
    narrative TEXT,
    groq_model TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS report_artifacts_created_idx ON report_artifacts (created_at DESC);

-- Serving views: Phase 5 API reads the same snapshot numbers as v_ranked_themes.
CREATE OR REPLACE VIEW v_api_theme_cards AS
SELECT * FROM v_ranked_themes;

CREATE OR REPLACE VIEW v_api_trend_series AS
SELECT
    m.cluster_run_id,
    m.theme_id,
    t.name AS theme_name,
    m.slice ->> 'bucket' AS bucket,
    m.mention_count,
    m.share_of_voice,
    m.data_confidence,
    m.unavailable_sources,
    m.eligible_corpus_count,
    m.trend_direction
FROM theme_metrics m
JOIN themes t ON t.id = m.theme_id AND t.cluster_run_id = m.cluster_run_id
WHERE m.slice_kind = 'time_bucket'
  AND t.published IS TRUE;
