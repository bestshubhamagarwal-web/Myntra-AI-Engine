-- Phase 4: clustering, theme metrics, impact score (Architecture §8.3–8.6, §9.2).
-- Single formula source for Phase 5 API. Do not interpolate unavailable sources.

CREATE TABLE IF NOT EXISTS cluster_runs (
    id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding_model TEXT,
    embedding_revision TEXT,
    groq_model_light TEXT,
    prompt_version TEXT,
    corpus TEXT,
    n_documents INTEGER,
    n_clustered INTEGER,
    n_noise INTEGER,
    n_themes INTEGER,
    n_incremental INTEGER,
    c_max INTEGER,
    s_max INTEGER,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS themes (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    hypothesis_flag BOOLEAN NOT NULL DEFAULT TRUE,
    bookmark_vs_stall TEXT NOT NULL DEFAULT 'unclear',
    published BOOLEAN NOT NULL DEFAULT FALSE,
    label_status TEXT NOT NULL DEFAULT 'pending',
    cluster_run_id UUID REFERENCES cluster_runs (id),
    centroid vector(1024),
    hdbscan_label INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS themes_cluster_run_idx ON themes (cluster_run_id);
CREATE INDEX IF NOT EXISTS themes_published_idx ON themes (published);

CREATE TABLE IF NOT EXISTS document_themes (
    document_id UUID NOT NULL REFERENCES normalized_documents (id),
    theme_id UUID NOT NULL REFERENCES themes (id),
    cluster_run_id UUID NOT NULL REFERENCES cluster_runs (id),
    assignment_confidence REAL,
    assignment_method TEXT NOT NULL,
    PRIMARY KEY (document_id, theme_id, cluster_run_id)
);

CREATE INDEX IF NOT EXISTS document_themes_theme_idx ON document_themes (theme_id);
CREATE INDEX IF NOT EXISTS document_themes_run_idx ON document_themes (cluster_run_id);

CREATE TABLE IF NOT EXISTS theme_metrics (
    id UUID PRIMARY KEY,
    theme_id UUID NOT NULL REFERENCES themes (id),
    cluster_run_id UUID NOT NULL REFERENCES cluster_runs (id),
    slice_kind TEXT NOT NULL,
    slice JSONB NOT NULL,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    mention_count INTEGER NOT NULL DEFAULT 0,
    eligible_corpus_count INTEGER NOT NULL DEFAULT 0,
    share_of_voice DOUBLE PRECISION NOT NULL DEFAULT 0,
    source_diversity INTEGER NOT NULL DEFAULT 0,
    independent_source_density INTEGER NOT NULL DEFAULT 0,
    sentiment_skew DOUBLE PRECISION,
    sentiment_severity DOUBLE PRECISION,
    trend_direction TEXT,
    segment_concentration DOUBLE PRECISION,
    segment_breadth DOUBLE PRECISION,
    data_confidence DOUBLE PRECISION,
    impact_score DOUBLE PRECISION,
    unavailable_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    denominator_definition TEXT NOT NULL,
    mean_extraction_confidence DOUBLE PRECISION,
    c_max INTEGER NOT NULL,
    s_max INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS theme_metrics_run_theme_slice_uidx
    ON theme_metrics (cluster_run_id, theme_id, slice_kind, slice);

CREATE INDEX IF NOT EXISTS theme_metrics_theme_idx ON theme_metrics (theme_id);
CREATE INDEX IF NOT EXISTS theme_metrics_slice_kind_idx ON theme_metrics (slice_kind);

-- Frozen formulas (Architecture §8.4–8.6). Phase 5 API must reuse these, not re-derive.

CREATE OR REPLACE FUNCTION metric_share_of_voice(
    mention_count INTEGER,
    eligible_corpus_count INTEGER
) RETURNS DOUBLE PRECISION
LANGUAGE SQL
IMMUTABLE
AS $$
  SELECT CASE
    WHEN eligible_corpus_count IS NULL OR eligible_corpus_count <= 0 THEN 0::DOUBLE PRECISION
    ELSE mention_count::DOUBLE PRECISION / eligible_corpus_count::DOUBLE PRECISION
  END
$$;

CREATE OR REPLACE FUNCTION metric_data_confidence(
    mention_count INTEGER,
    source_diversity INTEGER,
    mean_extraction_confidence DOUBLE PRECISION,
    c_max INTEGER,
    s_max INTEGER
) RETURNS DOUBLE PRECISION
LANGUAGE SQL
IMMUTABLE
AS $$
  SELECT GREATEST(0::DOUBLE PRECISION, LEAST(1::DOUBLE PRECISION,
    0.4 * LN(1 + COALESCE(mention_count, 0))
        / LN(1 + GREATEST(COALESCE(c_max, 200), 1))
    + 0.3 * LEAST(
        COALESCE(source_diversity, 0)::DOUBLE PRECISION
        / GREATEST(COALESCE(s_max, 4), 1)::DOUBLE PRECISION,
        1::DOUBLE PRECISION
      )
    + 0.3 * GREATEST(0::DOUBLE PRECISION, LEAST(1::DOUBLE PRECISION, COALESCE(mean_extraction_confidence, 0)))
  ))
$$;

CREATE OR REPLACE FUNCTION metric_impact_score(
    share_of_voice DOUBLE PRECISION,
    sentiment_severity DOUBLE PRECISION,
    segment_breadth DOUBLE PRECISION,
    data_confidence DOUBLE PRECISION
) RETURNS DOUBLE PRECISION
LANGUAGE SQL
IMMUTABLE
AS $$
  SELECT COALESCE(share_of_voice, 0) * COALESCE(sentiment_severity, 0)
       * COALESCE(segment_breadth, 0) * COALESCE(data_confidence, 0)
$$;

-- Eligible corpus after relevance + quality (SoV denominator). Failed Groq JSON
-- stays in the denominator if the doc is eligible, but never in mention_count.
CREATE OR REPLACE VIEW v_eligible_corpus AS
SELECT
    n.id AS document_id,
    r.source_type,
    r.author_hash,
    r.url,
    r.published_at,
    r.platform,
    n.product_category,
    n.gender_segment,
    n.price_tier,
    n.platform_used,
    n.occasion,
    n.review_date,
    n.quality_score,
    n.eligible,
    n.intent_mode,
    e.extraction_status,
    e.metrics_eligible
FROM normalized_documents n
JOIN raw_documents r ON r.id = n.raw_id
LEFT JOIN extractions e ON e.document_id = n.id
WHERE n.eligible IS TRUE
  AND n.duplicate_of IS NULL;

CREATE OR REPLACE VIEW v_theme_metrics_formula AS
SELECT
    m.*,
    metric_share_of_voice(m.mention_count, m.eligible_corpus_count) AS share_of_voice_formula,
    metric_data_confidence(
        m.mention_count,
        m.source_diversity,
        m.mean_extraction_confidence,
        m.c_max,
        m.s_max
    ) AS data_confidence_formula,
    metric_impact_score(
        m.share_of_voice,
        m.sentiment_severity,
        m.segment_breadth,
        m.data_confidence
    ) AS impact_score_formula
FROM theme_metrics m;

CREATE OR REPLACE VIEW v_ranked_themes AS
SELECT
    t.id AS theme_id,
    t.name,
    t.description,
    t.hypothesis_flag,
    t.bookmark_vs_stall,
    t.published,
    t.cluster_run_id,
    t.created_at,
    cr.algorithm,
    cr.corpus,
    cr.started_at AS themes_refreshed_at,
    m.mention_count,
    m.share_of_voice,
    m.source_diversity,
    m.independent_source_density,
    m.sentiment_skew,
    m.sentiment_severity,
    m.trend_direction,
    m.segment_concentration,
    m.segment_breadth,
    m.data_confidence,
    m.impact_score,
    m.unavailable_sources,
    m.eligible_corpus_count,
    m.denominator_definition,
    m.c_max,
    m.s_max
FROM themes t
JOIN cluster_runs cr ON cr.id = t.cluster_run_id
JOIN theme_metrics m
  ON m.theme_id = t.id
 AND m.cluster_run_id = t.cluster_run_id
 AND m.slice_kind = 'global'
WHERE t.published IS TRUE
  AND cr.status = 'success'
  AND cr.id = (
      SELECT id FROM cluster_runs
      WHERE status = 'success'
      ORDER BY started_at DESC
      LIMIT 1
  );

CREATE OR REPLACE VIEW v_theme_evidence AS
SELECT
    t.id AS theme_id,
    t.name,
    t.cluster_run_id,
    dt.document_id,
    dt.assignment_method,
    dt.assignment_confidence,
    n.product_category,
    n.intent_mode,
    r.source_type,
    r.url,
    (r.url IS NULL OR btrim(r.url) = '') AS link_unavailable,
    COALESCE(q.elem ->> 'span', q.elem ->> 'text') AS quote_span,
    r.published_at,
    n.review_date
FROM themes t
JOIN document_themes dt
  ON dt.theme_id = t.id
 AND dt.cluster_run_id = t.cluster_run_id
JOIN normalized_documents n ON n.id = dt.document_id
JOIN raw_documents r ON r.id = n.raw_id
JOIN extractions e ON e.document_id = n.id
LEFT JOIN LATERAL jsonb_array_elements(COALESCE(e.verbatim_quotes, '[]'::jsonb)) AS q(elem) ON TRUE
WHERE t.published IS TRUE;
