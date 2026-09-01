INSERT INTO source_config (source_type, enabled, notes) VALUES
    ('play_store', TRUE, 'Myntra Android app reviews'),
    ('app_store', FALSE, 'Phase 3'),
    ('reddit', FALSE, 'Phase 3'),
    ('youtube', FALSE, 'Phase 3'),
    ('x', FALSE, 'optional'),
    ('quora', FALSE, 'optional fifth source'),
    ('forum', FALSE, 'optional'),
    ('instagram', FALSE, 'unavailable until a public ToS-compliant path exists'),
    ('facebook', FALSE, 'unavailable until a public ToS-compliant path exists'),
    ('myntra_qa', FALSE, 'optional fifth source'),
    ('myntra_review', FALSE, 'optional fifth source')
ON CONFLICT (source_type) DO NOTHING;

-- Query seeds drive later Reddit/YouTube/X connectors. "Myntra vs AJIO" is
-- comparison talk inside Myntra-relevant threads, not a competitor-app crawl.
INSERT INTO ingest_queries (query_text, source_type, active) VALUES
    ('Myntra wishlist', NULL, TRUE),
    ('Myntra cart', NULL, TRUE),
    ('Myntra sizing', NULL, TRUE),
    ('Myntra returns', NULL, TRUE),
    ('Myntra vs AJIO', NULL, TRUE);
