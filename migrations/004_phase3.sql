-- Phase 3: enable implemented connectors, honest unavailable notes, YouTube query seeds,
-- and source_status counts (real zeros, never imputed volumes).

UPDATE source_config SET
    enabled = TRUE,
    notes = 'Myntra Android app reviews'
WHERE source_type = 'play_store';

UPDATE source_config SET
    enabled = TRUE,
    notes = 'Myntra iOS app reviews (official iTunes customer-reviews RSS)'
WHERE source_type = 'app_store';

UPDATE source_config SET
    enabled = TRUE,
    notes = 'PRAW or public Reddit JSON; subreddits + site search; Myntra-filtered'
WHERE source_type = 'reddit';

UPDATE source_config SET
    enabled = TRUE,
    notes = 'YouTube Data API comments on haul / size-guide / vs / unboxing videos mentioning Myntra'
WHERE source_type = 'youtube';

UPDATE source_config SET
    enabled = FALSE,
    notes = 'X API v2 recent search (optional fifth source; unavailable without bearer token)'
WHERE source_type = 'x';

UPDATE source_config SET
    enabled = FALSE,
    notes = 'unavailable — no ToS-clear public API in Phase 3'
WHERE source_type = 'quora';

UPDATE source_config SET
    enabled = FALSE,
    notes = 'unavailable — no ToS-clear public connector in Phase 3'
WHERE source_type = 'forum';

UPDATE source_config SET
    enabled = FALSE,
    notes = 'unavailable until a public ToS-compliant path exists'
WHERE source_type IN ('instagram', 'facebook');

UPDATE source_config SET
    enabled = FALSE,
    notes = 'unavailable — Myntra on-site Q&A not ingested (ToS)'
WHERE source_type = 'myntra_qa';

UPDATE source_config SET
    enabled = FALSE,
    notes = 'unavailable — Myntra on-site reviews not ingested (ToS)'
WHERE source_type = 'myntra_review';

INSERT INTO source_config (source_type, enabled, notes) VALUES
    ('other', FALSE, 'unavailable — reserved enum; not a live connector')
ON CONFLICT (source_type) DO UPDATE SET
    notes = EXCLUDED.notes,
    enabled = FALSE;

INSERT INTO ingest_queries (query_text, source_type, active)
SELECT v.query_text, v.source_type, TRUE
FROM (
    VALUES
        ('Myntra haul', 'youtube'),
        ('Myntra try-on', 'youtube'),
        ('Myntra size guide', 'youtube'),
        ('Myntra unboxing', 'youtube')
) AS v(query_text, source_type)
WHERE NOT EXISTS (
    SELECT 1 FROM ingest_queries q
    WHERE q.query_text = v.query_text AND q.source_type = v.source_type
);

CREATE OR REPLACE VIEW source_status AS
SELECT
    sc.source_type,
    CASE
        WHEN sc.enabled IS FALSE THEN 'unavailable'
        WHEN lr.status = 'failed' THEN 'failed'
        WHEN lr.status IN ('skipped_disabled', 'skipped_unconfigured') THEN 'unavailable'
        WHEN lr.status = 'success' THEN 'live'
        ELSE 'unavailable'
    END AS status,
    sc.enabled,
    sc.notes,
    lr.id AS last_run_id,
    lr.status AS last_run_status,
    lr.finished_at AS last_run_finished_at,
    lr.rows_fetched AS last_rows_fetched,
    lr.source_available AS last_source_available,
    COALESCE(raw.n, 0) AS raw_count,
    COALESCE(norm.n, 0) AS normalized_count
FROM source_config sc
LEFT JOIN LATERAL (
    SELECT *
    FROM ingest_runs ir
    WHERE ir.source_type = sc.source_type
    ORDER BY ir.started_at DESC
    LIMIT 1
) lr ON TRUE
LEFT JOIN (
    SELECT source_type, COUNT(*) AS n
    FROM raw_documents
    GROUP BY source_type
) raw ON raw.source_type = sc.source_type
LEFT JOIN (
    SELECT r.source_type, COUNT(*) AS n
    FROM normalized_documents n
    JOIN raw_documents r ON r.id = n.raw_id
    GROUP BY r.source_type
) norm ON norm.source_type = sc.source_type;
