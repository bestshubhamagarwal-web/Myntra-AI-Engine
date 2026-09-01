-- Phase 7: overlapping ingest lock status must not flip a live source to unavailable.
-- skipped_locked / running are in-flight signals, not source outages (EC-IN-16).

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
      AND ir.status NOT IN ('skipped_locked', 'running')
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
