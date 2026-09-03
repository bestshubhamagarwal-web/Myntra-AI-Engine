"""Copy a laptop pickle corpus into hosted Postgres (Neon) for Vercel deploys."""

from __future__ import annotations

import logging
from typing import Callable, Iterable, Sequence, TypeVar

from psycopg.types.json import Json

from src.db.local import PersistentMemoryRepository
from src.db.memory import MemoryRepository
from src.db.postgres import PostgresRepository, _extraction_values
from src.db.repository import ClusterRun, IngestRun, NormalizedRecord
from src.models.envelope import RawEnvelope

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]
BATCH_SIZE = 500
T = TypeVar("T")


def _progress(fn: ProgressFn | None, label: str, done: int, total: int) -> None:
    if fn is not None:
        fn(label, done, total)
    elif done == total or done % 1000 == 0 or total <= 20:
        log.info("%s %s/%s", label, done, total)


def _batched(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def sync_local_to_postgres(
    local: MemoryRepository,
    target: PostgresRepository,
    *,
    progress: ProgressFn | None = None,
) -> dict[str, int]:
    """Upsert raw/normalized/extract/cluster rows from a local store into Postgres."""
    counts: dict[str, int] = {}

    for source_type, cfg in sorted(local.source_config.items()):
        target.set_enabled(source_type, bool(cfg.get("enabled")))

    runs = sorted(local.ingest_runs.values(), key=lambda item: item.started_at or item.finished_at)
    _batch_ingest_runs(target, runs)
    counts["ingest_runs"] = len(runs)
    _progress(progress, "ingest_runs", len(runs), len(runs))

    _batch_normalize_runs(target, local.normalize_runs)
    counts["normalize_runs"] = len(local.normalize_runs)
    _progress(progress, "normalize_runs", len(local.normalize_runs), len(local.normalize_runs))

    raw_items = list(local.raw.values())
    _batch_raw(target, raw_items, progress)
    counts["raw"] = len(raw_items)

    norm_items = list(local.normalized.values())
    _batch_normalized(target, norm_items, progress)
    counts["normalized"] = len(norm_items)

    extract_items = list(local.extractions.values())
    _batch_extractions(target, extract_items, progress)
    counts["extractions"] = len(extract_items)

    run = local.latest_cluster_run(success_only=True)
    if run is not None:
        counts.update(_sync_cluster(local, target, run, progress=progress))

    return counts


def _batch_ingest_runs(target: PostgresRepository, runs: list[IngestRun]) -> None:
    if not runs:
        return
    sql = """
        INSERT INTO ingest_runs (
            id, source_type, status, started_at, finished_at,
            rows_fetched, rows_upserted, watermark_before, watermark_after,
            error_message, source_available, payload_warning
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (id) DO UPDATE SET
            source_type = EXCLUDED.source_type,
            status = EXCLUDED.status,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            rows_fetched = EXCLUDED.rows_fetched,
            rows_upserted = EXCLUDED.rows_upserted,
            watermark_before = EXCLUDED.watermark_before,
            watermark_after = EXCLUDED.watermark_after,
            error_message = EXCLUDED.error_message,
            source_available = EXCLUDED.source_available,
            payload_warning = EXCLUDED.payload_warning
    """
    rows = [
        (
            str(run.id),
            run.source_type,
            run.status,
            run.started_at,
            run.finished_at,
            run.rows_fetched,
            run.rows_upserted,
            run.watermark_before,
            run.watermark_after,
            run.error_message,
            run.source_available,
            run.payload_warning,
        )
        for run in runs
    ]
    with target.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()


def _batch_normalize_runs(target: PostgresRepository, runs: dict) -> None:
    if not runs:
        return
    sql = """
        INSERT INTO normalize_runs (
            id, started_at, finished_at, since_ingest_run_id,
            rows_accepted, rows_rejected, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            since_ingest_run_id = EXCLUDED.since_ingest_run_id,
            rows_accepted = EXCLUDED.rows_accepted,
            rows_rejected = EXCLUDED.rows_rejected,
            status = EXCLUDED.status
    """
    rows = []
    for run_id, payload in runs.items():
        since = payload.get("since_ingest_run_id")
        rows.append(
            (
                str(run_id),
                payload.get("started_at"),
                payload.get("finished_at"),
                str(since) if since else None,
                payload.get("rows_accepted"),
                payload.get("rows_rejected"),
                payload.get("status") or "success",
            )
        )
    with target.connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()


def _raw_row(envelope: RawEnvelope) -> tuple:
    return (
        str(envelope.id),
        envelope.source_type.value,
        envelope.source_id,
        envelope.url,
        envelope.fetched_at,
        envelope.published_at,
        envelope.platform,
        envelope.raw_text,
        envelope.raw_title,
        envelope.star_rating,
        Json(envelope.parent_context),
        envelope.author_hash,
        envelope.payload_uri,
        envelope.myntra_relevance.value if envelope.myntra_relevance else None,
        envelope.reject_reason,
        envelope.content_hash,
        str(envelope.ingest_run_id) if envelope.ingest_run_id else None,
        envelope.date_anomaly,
    )


def _batch_raw(
    target: PostgresRepository,
    items: list[RawEnvelope],
    progress: ProgressFn | None,
) -> None:
    if not items:
        return
    sql = """
        INSERT INTO raw_documents (
            id, source_type, source_id, url, fetched_at, published_at,
            platform, raw_text, raw_title, star_rating, parent_context,
            author_hash, payload_uri, myntra_relevance, reject_reason,
            content_hash, ingest_run_id, date_anomaly
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (source_type, source_id) DO UPDATE SET
            url = EXCLUDED.url,
            fetched_at = EXCLUDED.fetched_at,
            published_at = EXCLUDED.published_at,
            platform = EXCLUDED.platform,
            raw_text = EXCLUDED.raw_text,
            raw_title = EXCLUDED.raw_title,
            star_rating = EXCLUDED.star_rating,
            parent_context = EXCLUDED.parent_context,
            author_hash = EXCLUDED.author_hash,
            payload_uri = COALESCE(EXCLUDED.payload_uri, raw_documents.payload_uri),
            myntra_relevance = COALESCE(EXCLUDED.myntra_relevance, raw_documents.myntra_relevance),
            content_hash = EXCLUDED.content_hash,
            ingest_run_id = EXCLUDED.ingest_run_id,
            date_anomaly = EXCLUDED.date_anomaly
    """
    done = 0
    total = len(items)
    with target.connect() as conn:
        with conn.cursor() as cur:
            for batch in _batched(items, BATCH_SIZE):
                cur.executemany(sql, [_raw_row(item) for item in batch])
                conn.commit()
                done += len(batch)
                _progress(progress, "raw", done, total)


def _norm_row(record: NormalizedRecord) -> tuple:
    return (
        str(record.id),
        str(record.raw_id),
        record.text_original,
        record.text_en,
        record.language,
        record.product_category,
        record.gender_segment,
        record.price_tier,
        record.platform_used,
        record.occasion,
        record.star_rating,
        record.review_date,
        record.quality_score,
        record.content_hash,
        str(record.duplicate_of) if record.duplicate_of else None,
        record.eligible,
        record.pii_scrubbed_at,
        str(record.normalize_run_id) if record.normalize_run_id else None,
        record.intent_mode,
    )


def _batch_normalized(
    target: PostgresRepository,
    items: list[NormalizedRecord],
    progress: ProgressFn | None,
) -> None:
    if not items:
        return
    sql = """
        INSERT INTO normalized_documents (
            id, raw_id, text_original, text_en, language,
            product_category, gender_segment, price_tier, platform_used,
            occasion, star_rating, review_date, quality_score, content_hash,
            duplicate_of, eligible, pii_scrubbed_at, normalize_run_id, intent_mode
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (raw_id) DO UPDATE SET
            text_original = EXCLUDED.text_original,
            text_en = EXCLUDED.text_en,
            language = EXCLUDED.language,
            product_category = EXCLUDED.product_category,
            gender_segment = EXCLUDED.gender_segment,
            price_tier = EXCLUDED.price_tier,
            platform_used = EXCLUDED.platform_used,
            occasion = EXCLUDED.occasion,
            star_rating = EXCLUDED.star_rating,
            review_date = EXCLUDED.review_date,
            quality_score = EXCLUDED.quality_score,
            content_hash = EXCLUDED.content_hash,
            duplicate_of = EXCLUDED.duplicate_of,
            eligible = EXCLUDED.eligible,
            pii_scrubbed_at = EXCLUDED.pii_scrubbed_at,
            normalize_run_id = EXCLUDED.normalize_run_id,
            intent_mode = EXCLUDED.intent_mode
    """
    done = 0
    total = len(items)
    with target.connect() as conn:
        with conn.cursor() as cur:
            for batch in _batched(items, BATCH_SIZE):
                cur.executemany(sql, [_norm_row(item) for item in batch])
                conn.commit()
                done += len(batch)
                _progress(progress, "normalized", done, total)


def _batch_extractions(
    target: PostgresRepository,
    items: list,
    progress: ProgressFn | None,
) -> None:
    if not items:
        return
    sql = """
        INSERT INTO extractions (
            document_id, content_hash, prompt_version, groq_model,
            extraction_status, intent_tag, intent_mode, friction_tags,
            residual_uncertainties, comparison_behavior,
            off_platform_info_seeking, entities, sentiment_primary,
            sentiment_severity, verbatim_quotes, maps_to_questions,
            extraction_confidence, raw_response, error_message,
            retry_count, prompt_tokens, completion_tokens, extracted_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            now()
        )
        ON CONFLICT (document_id) DO UPDATE SET
            content_hash = EXCLUDED.content_hash,
            prompt_version = EXCLUDED.prompt_version,
            groq_model = EXCLUDED.groq_model,
            extraction_status = EXCLUDED.extraction_status,
            intent_tag = EXCLUDED.intent_tag,
            intent_mode = EXCLUDED.intent_mode,
            friction_tags = EXCLUDED.friction_tags,
            residual_uncertainties = EXCLUDED.residual_uncertainties,
            comparison_behavior = EXCLUDED.comparison_behavior,
            off_platform_info_seeking = EXCLUDED.off_platform_info_seeking,
            entities = EXCLUDED.entities,
            sentiment_primary = EXCLUDED.sentiment_primary,
            sentiment_severity = EXCLUDED.sentiment_severity,
            verbatim_quotes = EXCLUDED.verbatim_quotes,
            maps_to_questions = EXCLUDED.maps_to_questions,
            extraction_confidence = EXCLUDED.extraction_confidence,
            raw_response = EXCLUDED.raw_response,
            error_message = EXCLUDED.error_message,
            retry_count = EXCLUDED.retry_count,
            prompt_tokens = EXCLUDED.prompt_tokens,
            completion_tokens = EXCLUDED.completion_tokens,
            extracted_at = EXCLUDED.extracted_at,
            updated_at = now()
    """
    done = 0
    total = len(items)
    with target.connect() as conn:
        with conn.cursor() as cur:
            for batch in _batched(items, BATCH_SIZE):
                cur.executemany(sql, [_extraction_values(item) for item in batch])
                conn.commit()
                done += len(batch)
                _progress(progress, "extractions", done, total)


def _sync_cluster(
    local: MemoryRepository,
    target: PostgresRepository,
    run: ClusterRun,
    *,
    progress: ProgressFn | None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    # Re-sync must be idempotent: Neon may already have the cluster_run row from a
    # partial earlier push (themes without document_themes / theme_metrics).
    if target.get_cluster_run(run.id) is None:
        target.start_cluster_run(run)

    themes = [theme for theme in local.themes.values() if theme.cluster_run_id == run.id]
    total = len(themes)
    for index, theme in enumerate(themes, start=1):
        target.upsert_theme(theme)
        _progress(progress, "themes", index, total)
    counts["themes"] = total

    doc_themes = [row for row in local.document_themes if row.cluster_run_id == run.id]
    _progress(progress, "document_themes", 0, len(doc_themes))
    target.replace_document_themes(run.id, doc_themes)
    _progress(progress, "document_themes", len(doc_themes), len(doc_themes))
    counts["document_themes"] = len(doc_themes)

    metrics = [row for row in local.theme_metrics if row.cluster_run_id == run.id]
    _progress(progress, "theme_metrics", 0, len(metrics))
    target.replace_theme_metrics(run.id, metrics)
    _progress(progress, "theme_metrics", len(metrics), len(metrics))
    counts["theme_metrics"] = len(metrics)

    ngrams = [row for row in local.ngrams if row.cluster_run_id == run.id]
    _progress(progress, "ngrams", 0, len(ngrams))
    target.replace_ngrams(run.id, ngrams)
    _progress(progress, "ngrams", len(ngrams), len(ngrams))
    counts["ngrams"] = len(ngrams)

    target.finish_cluster_run(run)
    counts["cluster_runs"] = 1
    return counts


def load_local_store(path) -> PersistentMemoryRepository:
    return PersistentMemoryRepository(path)
