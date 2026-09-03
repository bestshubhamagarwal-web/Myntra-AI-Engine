from __future__ import annotations

import re
from datetime import datetime
from threading import Lock
from typing import Any, Sequence
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from src.db.repository import (
    ChatMessage,
    ChatSession,
    ChunkRecord,
    ClusterRun,
    DocumentTheme,
    EmbedRun,
    ExtractionRecord,
    ExtractRun,
    IngestRun,
    NgramRow,
    NormalizedRecord,
    ReportArtifact,
    SourceStatus,
    ThemeMetricsSnapshot,
    ThemeRecord,
)
from src.models.envelope import MyntraRelevance, RawEnvelope, SourceType
from src.timeutil import as_uuid, coerce_aware

# Reuse warm Neon/TLS sessions across Query API calls in one Vercel isolate.
_POOLS: dict[str, "_ConnPool"] = {}
_POOLS_LOCK = Lock()
_POOL_MAX = 4
_WRITE_BATCH = 500


def _batched(items: Sequence[Any], size: int = _WRITE_BATCH):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class _PooledCheckout:
    """Context manager that returns a live connection to the pool on exit."""

    __slots__ = ("_pool", "_conn")

    def __init__(self, pool: "_ConnPool") -> None:
        self._pool = pool
        self._conn: psycopg.Connection | None = None

    def __enter__(self) -> psycopg.Connection:
        self._conn = self._pool.acquire()
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> None:
        conn = self._conn
        self._conn = None
        if conn is None:
            return None
        try:
            conn.rollback()
        except Exception:
            self._pool.discard(conn)
            return None
        self._pool.release(conn)
        return None


class _ConnPool:
    def __init__(self, database_url: str, *, max_size: int = _POOL_MAX) -> None:
        self.database_url = database_url
        self.max_size = max_size
        self._lock = Lock()
        self._idle: list[psycopg.Connection] = []

    def _open(self) -> psycopg.Connection:
        from src.db.connect import open_psycopg, postgres_connect

        try:
            return open_psycopg(
                self.database_url,
                row_factory=dict_row,
                connect_timeout=8,
            )
        except Exception:
            return postgres_connect(
                self.database_url,
                row_factory=dict_row,
                connect_timeout=8,
            )

    def acquire(self) -> psycopg.Connection:
        with self._lock:
            while self._idle:
                conn = self._idle.pop()
                if not conn.closed:
                    return conn
        return self._open()

    def release(self, conn: psycopg.Connection) -> None:
        if conn.closed:
            return
        with self._lock:
            if len(self._idle) < self.max_size:
                self._idle.append(conn)
                return
        try:
            conn.close()
        except Exception:
            pass

    def discard(self, conn: psycopg.Connection) -> None:
        try:
            if not conn.closed:
                conn.close()
        except Exception:
            pass

    def connection(self) -> _PooledCheckout:
        return _PooledCheckout(self)


def _pool_for(database_url: str) -> _ConnPool:
    with _POOLS_LOCK:
        pool = _POOLS.get(database_url)
        if pool is None:
            pool = _ConnPool(database_url)
            _POOLS[database_url] = pool
        return pool


class PostgresRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def connect(self) -> _PooledCheckout:
        return _pool_for(self.database_url).connection()

    def is_enabled(self, source_type: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM source_config WHERE source_type = %s",
                (source_type,),
            ).fetchone()
        return bool(row and row["enabled"])

    def set_enabled(self, source_type: str, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_config (source_type, enabled)
                VALUES (%s, %s)
                ON CONFLICT (source_type) DO UPDATE SET enabled = EXCLUDED.enabled
                """,
                (source_type, enabled),
            )
            conn.commit()

    def start_ingest_run(self, run: IngestRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ingest_runs (
                    id, source_type, status, started_at, source_available
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (str(run.id), run.source_type, run.status, run.started_at, run.source_available),
            )
            conn.commit()

    def finish_ingest_run(self, run: IngestRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ingest_runs SET
                    status = %s,
                    finished_at = %s,
                    rows_fetched = %s,
                    rows_upserted = %s,
                    watermark_before = %s,
                    watermark_after = %s,
                    error_message = %s,
                    source_available = %s,
                    payload_warning = %s
                WHERE id = %s
                """,
                (
                    run.status,
                    run.finished_at,
                    run.rows_fetched,
                    run.rows_upserted,
                    run.watermark_before,
                    run.watermark_after,
                    run.error_message,
                    run.source_available,
                    run.payload_warning,
                    str(run.id),
                ),
            )
            conn.commit()

    def get_watermark(self, source_type: str) -> Any:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(published_at) AS watermark
                FROM raw_documents
                WHERE source_type = %s
                  AND date_anomaly IS FALSE
                  AND published_at IS NOT NULL
                  AND published_at <= now()
                """,
                (source_type,),
            ).fetchone()
        return coerce_aware(row["watermark"]) if row and row["watermark"] else None

    def upsert_raw(self, envelope: RawEnvelope) -> tuple[Any, bool]:
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM raw_documents
                WHERE source_type = %s AND source_id = %s
                """,
                (envelope.source_type.value, envelope.source_id),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE raw_documents SET
                        url = %s,
                        fetched_at = %s,
                        published_at = %s,
                        platform = %s,
                        raw_text = %s,
                        raw_title = %s,
                        star_rating = %s,
                        parent_context = %s,
                        author_hash = %s,
                        payload_uri = COALESCE(%s, payload_uri),
                        content_hash = %s,
                        ingest_run_id = %s,
                        date_anomaly = %s,
                        myntra_relevance = COALESCE(%s, myntra_relevance),
                        reject_reason = CASE
                            WHEN %s IS DISTINCT FROM content_hash THEN NULL
                            ELSE reject_reason
                        END
                    WHERE source_type = %s AND source_id = %s
                    """,
                    (
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
                        envelope.content_hash,
                        str(envelope.ingest_run_id) if envelope.ingest_run_id else None,
                        envelope.date_anomaly,
                        envelope.myntra_relevance.value if envelope.myntra_relevance else None,
                        envelope.content_hash,
                        envelope.source_type.value,
                        envelope.source_id,
                    ),
                )
                conn.commit()
                return as_uuid(existing["id"]), False

            conn.execute(
                """
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
                """,
                (
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
                ),
            )
            conn.commit()
            return envelope.id, True

    def get_raw(self, raw_id) -> RawEnvelope | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM raw_documents WHERE id = %s", (str(raw_id),)
            ).fetchone()
        return _row_to_envelope(row) if row else None

    def get_raw_batch(self, raw_ids: set) -> dict[Any, RawEnvelope]:
        ids = [str(raw_id) for raw_id in raw_ids if raw_id]
        if not ids:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM raw_documents WHERE id = ANY(%s::uuid[])",
                (ids,),
            ).fetchall()
        return {as_uuid(row["id"]): _row_to_envelope(row) for row in rows}

    def list_normalized_matching(
        self,
        patterns: list[str],
        *,
        eligible_only: bool = True,
        limit: int = 400,
    ) -> list[NormalizedRecord]:
        cleaned = [item.strip() for item in patterns if (item or "").strip()]
        # Cap OR clauses — many ILIKE arms force sequential scans on Neon.
        cleaned = sorted(cleaned, key=len, reverse=True)[:6]
        if not cleaned or limit <= 0:
            return []
        # One regex is cheaper than N independent ILIKE predicates.
        pattern = "|".join(re.escape(item) for item in cleaned)
        sql = """
            SELECT n.*
            FROM normalized_documents n
            JOIN raw_documents r ON r.id = n.raw_id
            WHERE n.text_original ~* %s
        """
        params: list[Any] = [pattern]
        if eligible_only:
            sql += " AND n.eligible IS TRUE"
        sql += " ORDER BY n.pii_scrubbed_at DESC LIMIT %s"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_normalized(r) for r in rows]

    def list_raw(
        self,
        *,
        source_type: str | None = None,
        limit: int | None = None,
    ) -> list[RawEnvelope]:
        sql = "SELECT * FROM raw_documents WHERE 1=1"
        params: list[Any] = []
        if source_type:
            sql += " AND source_type = %s"
            params.append(source_type)
        sql += " ORDER BY fetched_at DESC NULLS LAST"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_envelope(r) for r in rows]

    def list_raw_for_run(self, ingest_run_id) -> list[RawEnvelope]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM raw_documents WHERE ingest_run_id = %s",
                (str(ingest_run_id),),
            ).fetchall()
        return [_row_to_envelope(r) for r in rows]

    def list_raw_pending_normalize(self) -> list[RawEnvelope]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.* FROM raw_documents r
                LEFT JOIN normalized_documents n ON n.raw_id = r.id
                WHERE n.id IS NULL
                  AND (r.myntra_relevance IS NULL OR r.myntra_relevance <> 'reject')
                """
            ).fetchall()
        return [_row_to_envelope(r) for r in rows]

    def list_stale_raw(self) -> list[RawEnvelope]:
        from src.normalize.text import expected_content_hash

        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, n.content_hash AS norm_hash
                FROM raw_documents r
                JOIN normalized_documents n ON n.raw_id = r.id
                """
            ).fetchall()
        out: list[RawEnvelope] = []
        for row in rows:
            env = _row_to_envelope(row)
            if expected_content_hash(env) != row["norm_hash"]:
                out.append(env)
        return out

    def list_raw_rejected(self, limit: int = 20) -> list[RawEnvelope]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM raw_documents
                WHERE myntra_relevance = 'reject'
                ORDER BY fetched_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [_row_to_envelope(r) for r in rows]

    def mark_raw_decision(self, raw_id, relevance: str, reject_reason: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE raw_documents
                SET myntra_relevance = %s, reject_reason = %s
                WHERE id = %s
                """,
                (relevance, reject_reason, str(raw_id)),
            )
            conn.commit()

    def start_normalize_run(self, run_id, started_at, since_ingest_run_id) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO normalize_runs (id, started_at, since_ingest_run_id, status)
                VALUES (%s, %s, %s, 'running')
                """,
                (
                    str(run_id),
                    started_at,
                    str(since_ingest_run_id) if since_ingest_run_id else None,
                ),
            )
            conn.commit()

    def finish_normalize_run(
        self, run_id, finished_at, rows_accepted: int, rows_rejected: int, status: str
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE normalize_runs SET
                    finished_at = %s,
                    rows_accepted = %s,
                    rows_rejected = %s,
                    status = %s
                WHERE id = %s
                """,
                (finished_at, rows_accepted, rows_rejected, status, str(run_id)),
            )
            conn.commit()

    def find_normalized_by_content_hash(self, content_hash: str):
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM normalized_documents
                WHERE content_hash = %s AND duplicate_of IS NULL
                LIMIT 1
                """,
                (content_hash,),
            ).fetchone()
        return as_uuid(row["id"]) if row else None

    def get_normalized_by_raw_id(self, raw_id) -> NormalizedRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM normalized_documents WHERE raw_id = %s",
                (str(raw_id),),
            ).fetchone()
        return _row_to_normalized(row) if row else None

    def upsert_normalized(self, record: NormalizedRecord) -> None:
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM normalized_documents WHERE raw_id = %s",
                (str(record.raw_id),),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE normalized_documents SET
                        text_original = %s,
                        text_en = %s,
                        language = %s,
                        product_category = %s,
                        gender_segment = %s,
                        price_tier = %s,
                        platform_used = %s,
                        occasion = %s,
                        star_rating = %s,
                        review_date = %s,
                        quality_score = %s,
                        content_hash = %s,
                        duplicate_of = %s,
                        eligible = %s,
                        pii_scrubbed_at = %s,
                        normalize_run_id = %s
                    WHERE raw_id = %s
                    """,
                    (
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
                        str(record.raw_id),
                    ),
                )
            else:
                conn.execute(
                    """
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
                    """,
                    (
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
                    ),
                )
            conn.commit()

    def count_raw(self, source_type: str | None = None) -> int:
        with self.connect() as conn:
            if source_type:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM raw_documents WHERE source_type = %s",
                    (source_type,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM raw_documents").fetchone()
        return int(row["n"]) if row else 0

    def count_normalized(self, source_type: str | None = None) -> int:
        with self.connect() as conn:
            if source_type:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM normalized_documents n
                    JOIN raw_documents r ON r.id = n.raw_id
                    WHERE r.source_type = %s
                    """,
                    (source_type,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS n FROM normalized_documents").fetchone()
        return int(row["n"]) if row else 0

    def overview_aggregates(self) -> dict[str, Any]:
        """Fast SQL aggregates for unfiltered overview (Vercel timeout safe)."""
        base = """
            FROM normalized_documents n
            JOIN raw_documents r ON r.id = n.raw_id
            LEFT JOIN extractions e ON e.document_id = n.id
            WHERE n.eligible IS TRUE AND n.duplicate_of IS NULL
        """
        with self.connect() as conn:
            eligible = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n {base}",
                ).fetchone()["n"]
            )
            by_source_rows = conn.execute(
                f"""
                SELECT r.source_type, COUNT(*) AS n
                {base}
                GROUP BY r.source_type
                ORDER BY r.source_type
                """
            ).fetchall()
            hist_rows = conn.execute(
                f"""
                SELECT
                    CONCAT(
                        to_char(COALESCE(n.review_date, r.published_at), 'IYYY'),
                        '-W',
                        lpad(to_char(COALESCE(n.review_date, r.published_at), 'IW'), 2, '0')
                    ) AS bucket,
                    COUNT(*) AS n
                {base}
                  AND COALESCE(n.review_date, r.published_at) IS NOT NULL
                GROUP BY 1
                ORDER BY 1
                """
            ).fetchall()
            tag_rows = conn.execute(
                f"""
                SELECT COALESCE(e.intent_tag, 'unknown') AS tag, COUNT(*) AS n
                {base}
                GROUP BY 1
                ORDER BY 1
                """
            ).fetchall()
            mode_rows = conn.execute(
                f"""
                SELECT COALESCE(e.intent_mode, n.intent_mode, 'unknown') AS mode, COUNT(*) AS n
                {base}
                GROUP BY 1
                ORDER BY 1
                """
            ).fetchall()
            raw_count = int(conn.execute("SELECT COUNT(*) AS n FROM raw_documents").fetchone()["n"])
            normalized_count = int(
                conn.execute("SELECT COUNT(*) AS n FROM normalized_documents").fetchone()["n"]
            )
            status_rows = conn.execute(
                "SELECT * FROM source_status ORDER BY source_type"
            ).fetchall()
            pull_rows = conn.execute(
                """
                SELECT DISTINCT ON (source_type)
                    source_type,
                    COALESCE(finished_at, started_at) AS pulled_at
                FROM ingest_runs
                WHERE status = 'success'
                ORDER BY source_type, COALESCE(finished_at, started_at) DESC
                """
            ).fetchall()
            latest_run = conn.execute(
                """
                SELECT *
                FROM ingest_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            cluster = conn.execute(
                """
                SELECT *
                FROM cluster_runs
                WHERE status = 'success'
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "eligible_corpus_count": eligible,
            "eligible_by_source": {row["source_type"]: int(row["n"]) for row in by_source_rows},
            "date_histogram": [
                {"bucket": row["bucket"], "count": int(row["n"])} for row in hist_rows if row["bucket"]
            ],
            "intent_tag_counts": {row["tag"]: int(row["n"]) for row in tag_rows},
            "intent_mode_counts": {row["mode"]: int(row["n"]) for row in mode_rows},
            "raw_count": raw_count,
            "normalized_count": normalized_count,
            "source_statuses": [
                SourceStatus(
                    source_type=r["source_type"],
                    status=r["status"],
                    enabled=r["enabled"],
                    notes=r["notes"],
                    last_run_id=as_uuid(r["last_run_id"]) if r["last_run_id"] else None,
                    last_run_status=r["last_run_status"],
                    last_run_finished_at=coerce_aware(r["last_run_finished_at"]),
                    last_rows_fetched=r["last_rows_fetched"],
                    last_source_available=r["last_source_available"],
                    raw_count=int(r["raw_count"]) if r.get("raw_count") is not None else 0,
                    normalized_count=int(r["normalized_count"])
                    if r.get("normalized_count") is not None
                    else 0,
                )
                for r in status_rows
            ],
            "last_successful_pulls": {
                row["source_type"]: coerce_aware(row["pulled_at"]) for row in pull_rows
            },
            "latest_ingest_run": _row_to_ingest_run(latest_run) if latest_run else None,
            "latest_cluster_run": _row_to_cluster_run(cluster) if cluster else None,
        }

    def first_chunk_ids(self, document_ids: set[UUID]) -> dict[UUID, UUID]:
        """Map document_id → first chunk id (ordinal ASC) in one round-trip."""
        ids = [str(doc_id) for doc_id in document_ids if doc_id]
        if not ids:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT ON (document_id) document_id, id
                FROM chunks
                WHERE document_id = ANY(%s::uuid[])
                ORDER BY document_id, ordinal ASC
                """,
                (ids,),
            ).fetchall()
        return {as_uuid(row["document_id"]): as_uuid(row["id"]) for row in rows}

    def segment_cross_tab(
        self,
        *,
        cluster_run_id: UUID,
        dimension: str,
    ) -> list[dict[str, Any]]:
        """Theme × segment mention counts without loading the full corpus into Python."""
        col_map = {
            "gender_segment": "COALESCE(NULLIF(n.gender_segment, ''), 'unknown')",
            "price_tier": "COALESCE(NULLIF(n.price_tier, ''), 'unknown')",
            "platform_used": "COALESCE(NULLIF(n.platform_used, ''), 'unknown')",
        }
        segment_expr = col_map.get(dimension)
        if segment_expr is None:
            return []
        with self.connect() as conn:
            denom_rows = conn.execute(
                f"""
                SELECT {segment_expr} AS segment, COUNT(*) AS n
                FROM normalized_documents n
                WHERE n.eligible IS TRUE AND n.duplicate_of IS NULL
                GROUP BY 1
                """
            ).fetchall()
            mention_rows = conn.execute(
                f"""
                SELECT
                    dt.theme_id,
                    {segment_expr} AS segment,
                    COUNT(*) AS n
                FROM document_themes dt
                JOIN normalized_documents n ON n.id = dt.document_id
                WHERE dt.cluster_run_id = %s
                  AND n.eligible IS TRUE
                  AND n.duplicate_of IS NULL
                GROUP BY dt.theme_id, 2
                """,
                (str(cluster_run_id),),
            ).fetchall()
        denom = {str(row["segment"] or "unknown"): int(row["n"]) for row in denom_rows}
        # Ensure unknown is always present for the UI contract.
        if "unknown" not in denom:
            denom["unknown"] = 0
        out: list[dict[str, Any]] = []
        for row in mention_rows:
            segment = str(row["segment"] or "unknown")
            out.append(
                {
                    "theme_id": as_uuid(row["theme_id"]),
                    "segment": segment,
                    "mention_count": int(row["n"]),
                    "eligible_corpus_count": int(denom.get(segment, 0)),
                }
            )
        # Emit zero cells for themes × missing unknown when needed by callers? Heatmap
        # builds from returned cells only — unknown column is forced via unknown_visible.
        return out

    def list_evidence_rows(
        self,
        *,
        cluster_run_id: UUID | None,
        theme_id: UUID | None = None,
        source_type: str | None = None,
        product_category: str | None = None,
        gender_segment: str | None = None,
        price_tier: str | None = None,
        platform_used: str | None = None,
        intent_mode: str | None = None,
        q: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Capped evidence join for dashboard tabs (avoids full-corpus Python scans)."""
        from src.normalize.pii import scrub_pii

        intent_aliases = {
            "bookmark": ["bookmark", "passive_bookmark"],
            "passive_bookmark": ["bookmark", "passive_bookmark"],
            "stall": ["stall", "near_term_purchase"],
            "near_term_purchase": ["stall", "near_term_purchase"],
            "unclear": ["unclear", "unknown", "mixed"],
            "unknown": ["unclear", "unknown", "mixed"],
            "mixed": ["mixed", "unclear"],
        }

        sql = """
            SELECT
                n.id AS document_id,
                n.text_original,
                n.product_category,
                n.intent_mode AS doc_intent_mode,
                r.source_type,
                r.url,
                COALESCE(n.review_date, r.published_at) AS published_at,
                e.intent_mode AS ext_intent_mode,
                e.intent_tag,
                e.friction_tags,
                e.sentiment_primary,
                e.maps_to_questions,
                e.verbatim_quotes,
                dt.theme_id,
                t.name AS theme_name,
                c.id AS chunk_id
            FROM normalized_documents n
            JOIN raw_documents r ON r.id = n.raw_id
            LEFT JOIN extractions e ON e.document_id = n.id
            LEFT JOIN document_themes dt
                ON dt.document_id = n.id
               AND (%s::uuid IS NULL OR dt.cluster_run_id = %s::uuid)
               AND (%s::uuid IS NULL OR dt.theme_id = %s::uuid)
            LEFT JOIN themes t ON t.id = dt.theme_id AND t.published IS TRUE
            LEFT JOIN LATERAL (
                SELECT id FROM chunks
                WHERE document_id = n.id
                ORDER BY ordinal ASC
                LIMIT 1
            ) c ON TRUE
            WHERE n.eligible IS TRUE AND n.duplicate_of IS NULL
        """
        params: list[Any] = [
            str(cluster_run_id) if cluster_run_id else None,
            str(cluster_run_id) if cluster_run_id else None,
            str(theme_id) if theme_id else None,
            str(theme_id) if theme_id else None,
        ]
        if theme_id is not None:
            sql += " AND dt.theme_id = %s"
            params.append(str(theme_id))
        if source_type:
            sql += " AND r.source_type = %s"
            params.append(source_type)
        if product_category:
            sql += " AND COALESCE(n.product_category, 'unknown') = %s"
            params.append(product_category)
        if gender_segment:
            sql += " AND COALESCE(n.gender_segment, 'unknown') = %s"
            params.append(gender_segment)
        if price_tier:
            sql += " AND COALESCE(NULLIF(n.price_tier, ''), 'unknown') = %s"
            params.append(price_tier)
        if platform_used:
            sql += " AND COALESCE(n.platform_used, 'unknown') = %s"
            params.append(platform_used)
        if intent_mode:
            aliases = sorted(intent_aliases.get(intent_mode, [intent_mode]))
            sql += (
                " AND COALESCE(e.intent_mode, n.intent_mode, 'unknown') = ANY(%s::text[])"
            )
            params.append(aliases)
        if q and q.strip():
            sql += " AND n.text_original ILIKE %s"
            params.append(f"%{q.strip()}%")
        sql += " ORDER BY COALESCE(n.review_date, r.published_at) DESC NULLS LAST LIMIT %s"
        # Over-fetch a bit so quote expansion still fills the requested limit.
        params.append(max(limit * 3, limit))

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str | None, str]] = set()
        for row in rows:
            quotes: list[str] = []
            verbatim = row.get("verbatim_quotes") or []
            if isinstance(verbatim, list):
                for item in verbatim:
                    if isinstance(item, dict):
                        span = item.get("span") or item.get("text")
                        if span:
                            quotes.append(str(span))
            if not quotes:
                text = (row.get("text_original") or "").strip()
                if text:
                    quotes.append(text[:280])
            doc_id = str(as_uuid(row["document_id"]))
            theme_uuid = as_uuid(row["theme_id"]) if row.get("theme_id") else None
            theme_id_s = str(theme_uuid) if theme_uuid else None
            chunk_id = str(as_uuid(row["chunk_id"])) if row.get("chunk_id") else None
            url = row.get("url")
            published = coerce_aware(row.get("published_at"))
            published_s = published.date().isoformat() if published else None
            friction = row.get("friction_tags") or []
            maps = row.get("maps_to_questions") or []
            for quote in quotes:
                if q and q.lower() not in quote.lower():
                    continue
                key = (doc_id, theme_id_s, quote)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "document_id": doc_id,
                        "chunk_id": chunk_id,
                        "theme_id": theme_id_s,
                        "theme_name": row.get("theme_name"),
                        "quote": scrub_pii(quote),
                        "source_type": row.get("source_type") or "unknown",
                        "url": url,
                        "link_unavailable": not bool(url and str(url).strip()),
                        "published_at": published_s,
                        "product_category": row.get("product_category"),
                        "intent_mode": row.get("ext_intent_mode") or row.get("doc_intent_mode"),
                        "intent_tag": row.get("intent_tag"),
                        "friction_tags": list(friction) if isinstance(friction, list) else [],
                        "sentiment": row.get("sentiment_primary"),
                        "maps_to_questions": list(maps) if isinstance(maps, list) else [],
                    }
                )
                if len(out) >= limit:
                    return out
        return out

    def list_source_status(self) -> list[SourceStatus]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM source_status ORDER BY source_type").fetchall()
        return [
            SourceStatus(
                source_type=r["source_type"],
                status=r["status"],
                enabled=r["enabled"],
                notes=r["notes"],
                last_run_id=as_uuid(r["last_run_id"]) if r["last_run_id"] else None,
                last_run_status=r["last_run_status"],
                last_run_finished_at=coerce_aware(r["last_run_finished_at"]),
                last_rows_fetched=r["last_rows_fetched"],
                last_source_available=r["last_source_available"],
                raw_count=int(r["raw_count"]) if r.get("raw_count") is not None else 0,
                normalized_count=int(r["normalized_count"])
                if r.get("normalized_count") is not None
                else 0,
            )
            for r in rows
        ]

    def list_ingest_queries(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT query_text, source_type, active FROM ingest_queries ORDER BY query_text"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_normalized(
        self,
        limit: int | None = 20,
        *,
        eligible_only: bool = False,
        random_sample: bool = False,
        source_type: str | None = None,
        copy: bool = True,
    ) -> list[NormalizedRecord]:
        del copy
        sql = """
            SELECT n.*
            FROM normalized_documents n
            JOIN raw_documents r ON r.id = n.raw_id
            WHERE 1=1
        """
        params: list[Any] = []
        if eligible_only:
            sql += " AND n.eligible IS TRUE"
        if source_type:
            sql += " AND r.source_type = %s"
            params.append(source_type)
        if random_sample:
            sql += " ORDER BY RANDOM()"
        else:
            sql += " ORDER BY n.pii_scrubbed_at DESC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_normalized(r) for r in rows]

    def get_normalized(self, document_id: UUID) -> NormalizedRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM normalized_documents WHERE id = %s",
                (str(document_id),),
            ).fetchone()
        return _row_to_normalized(row) if row else None

    def set_normalized_intent_mode(self, document_id: UUID, intent_mode: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE normalized_documents SET intent_mode = %s WHERE id = %s",
                (intent_mode, str(document_id)),
            )
            conn.commit()

    def list_extract_candidates(
        self,
        *,
        resume_after: UUID | None = None,
        limit: int | None = None,
        retry_failed: bool = True,
    ) -> list[NormalizedRecord]:
        sql = """
            SELECT n.*
            FROM normalized_documents n
            LEFT JOIN extractions e ON e.document_id = n.id
            WHERE n.eligible IS TRUE
              AND length(trim(n.text_original)) > 0
              AND (%s::uuid IS NULL OR n.id > %s::uuid)
              AND (
                    e.document_id IS NULL
                    OR e.extraction_status = 'pending'
                    OR e.content_hash IS DISTINCT FROM n.content_hash
                    OR (%s AND e.extraction_status = 'failed')
              )
            ORDER BY n.id
        """
        params: list[Any] = [
            str(resume_after) if resume_after else None,
            str(resume_after) if resume_after else None,
            retry_failed,
        ]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_normalized(r) for r in rows]

    def get_extraction(self, document_id: UUID) -> ExtractionRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM extractions WHERE document_id = %s",
                (str(document_id),),
            ).fetchone()
        return _row_to_extraction(row) if row else None

    def upsert_extraction(self, record: ExtractionRecord) -> None:
        values = _extraction_values(record)
        with self.connect() as conn:
            conn.execute(
                """
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
                """,
                values,
            )
            conn.commit()

    def list_extractions(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        metrics_eligible_only: bool = False,
        copy: bool = True,
    ) -> list[ExtractionRecord]:
        del copy
        sql = "SELECT * FROM extractions WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND extraction_status = %s"
            params.append(status)
        if metrics_eligible_only:
            sql += " AND metrics_eligible IS TRUE"
        sql += " ORDER BY document_id"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_extraction(r) for r in rows]

    def start_extract_run(self, run: ExtractRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO extract_runs (
                    id, started_at, status, prompt_version, groq_model,
                    resume_after_document_id
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(run.id),
                    run.started_at,
                    run.status,
                    run.prompt_version,
                    run.groq_model,
                    str(run.resume_after_document_id) if run.resume_after_document_id else None,
                ),
            )
            conn.commit()

    def finish_extract_run(self, run: ExtractRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE extract_runs SET
                    finished_at = %s,
                    status = %s,
                    rows_ok = %s,
                    rows_failed = %s,
                    rows_skipped = %s,
                    prompt_tokens = %s,
                    completion_tokens = %s,
                    error_message = %s
                WHERE id = %s
                """,
                (
                    run.finished_at,
                    run.status,
                    run.rows_ok,
                    run.rows_failed,
                    run.rows_skipped,
                    run.prompt_tokens,
                    run.completion_tokens,
                    run.error_message,
                    str(run.id),
                ),
            )
            conn.commit()

    def list_embed_candidates(self, *, limit: int | None = None) -> list[NormalizedRecord]:
        sql = """
            SELECT * FROM normalized_documents
            WHERE eligible IS TRUE
              AND length(trim(text_original)) > 0
            ORDER BY id
        """
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_normalized(r) for r in rows]

    def list_chunks(self, document_id: UUID | None = None, *, copy: bool = True) -> list[ChunkRecord]:
        del copy
        if document_id is not None:
            sql = "SELECT * FROM chunks WHERE document_id = %s ORDER BY ordinal"
            params: list[Any] = [str(document_id)]
        else:
            sql = "SELECT * FROM chunks ORDER BY document_id, ordinal"
            params = []
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_chunk(r) for r in rows]

    def replace_chunks(self, document_id: UUID, chunks: list[ChunkRecord]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = %s", (str(document_id),))
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO chunks (
                        id, document_id, ordinal, text, token_count,
                        embedding, embedding_model, embedding_revision, embedding_dim,
                        content_hash, source_type, published_at, product_category,
                        intent_tag, intent_mode, friction_tags, sentiment,
                        maps_to_questions, extraction_status
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s::vector, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        str(chunk.id),
                        str(chunk.document_id),
                        chunk.ordinal,
                        chunk.text,
                        chunk.token_count,
                        as_pgvector(chunk.embedding) if chunk.embedding is not None else None,
                        chunk.embedding_model,
                        chunk.embedding_revision,
                        chunk.embedding_dim,
                        chunk.content_hash,
                        chunk.source_type,
                        chunk.published_at,
                        chunk.product_category,
                        chunk.intent_tag,
                        chunk.intent_mode,
                        Json(chunk.friction_tags) if chunk.friction_tags is not None else None,
                        chunk.sentiment,
                        Json(chunk.maps_to_questions) if chunk.maps_to_questions is not None else None,
                        chunk.extraction_status,
                    ),
                )
            conn.commit()

    def update_chunk_embedding(
        self,
        chunk_id: UUID,
        embedding: list[float],
        *,
        embedding_model: str,
        embedding_revision: str | None,
        embedding_dim: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE chunks SET
                    embedding = %s::vector,
                    embedding_model = %s,
                    embedding_revision = %s,
                    embedding_dim = %s
                WHERE id = %s
                """,
                (
                    as_pgvector(embedding),
                    embedding_model,
                    embedding_revision,
                    embedding_dim,
                    str(chunk_id),
                ),
            )
            conn.commit()

    def update_chunk_metadata(self, document_id: UUID, extraction: ExtractionRecord) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE chunks SET
                    intent_tag = %s,
                    intent_mode = %s,
                    friction_tags = %s,
                    sentiment = %s,
                    maps_to_questions = %s,
                    extraction_status = %s
                WHERE document_id = %s
                """,
                (
                    extraction.intent_tag,
                    extraction.intent_mode,
                    Json(extraction.friction_tags),
                    extraction.sentiment_primary,
                    Json(extraction.maps_to_questions),
                    extraction.extraction_status,
                    str(document_id),
                ),
            )
            conn.commit()

    def distinct_embedding_models(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT embedding_model
                FROM chunks
                WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL
                """
            ).fetchall()
        return sorted(r["embedding_model"] for r in rows if r["embedding_model"])

    def nearest_chunks(
        self,
        query: list[float],
        *,
        k: int = 8,
        friction_tag: str | None = None,
        intent_mode: str | None = None,
        product_category: str | None = None,
        source_type: str | None = None,
        maps_to_question: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[ChunkRecord]:
        sql = """
            SELECT c.*,
                   1 - (c.embedding <=> %s::vector) AS similarity
            FROM chunks c
            WHERE c.embedding IS NOT NULL
              AND (%s::text IS NULL OR c.intent_mode = %s)
              AND (%s::text IS NULL OR c.friction_tags @> %s::jsonb)
              AND (%s::text IS NULL OR c.product_category = %s)
              AND (%s::text IS NULL OR c.source_type = %s)
              AND (%s::text IS NULL OR c.maps_to_questions @> %s::jsonb)
              AND (%s::timestamptz IS NULL OR c.published_at >= %s)
              AND (%s::timestamptz IS NULL OR c.published_at <= %s)
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """
        literal = as_pgvector(query)
        tag_json = Json([friction_tag]) if friction_tag else None
        maps_json = Json([maps_to_question]) if maps_to_question else None
        params = [
            literal,
            intent_mode,
            intent_mode,
            friction_tag,
            tag_json,
            product_category,
            product_category,
            source_type,
            source_type,
            maps_to_question,
            maps_json,
            date_from,
            date_from,
            date_to,
            date_to,
            literal,
            k,
        ]
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_chunk(r) for r in rows]

    def start_embed_run(self, run: EmbedRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO embed_runs (
                    id, started_at, status, embedding_model, embedding_revision, embedding_dim
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(run.id),
                    run.started_at,
                    run.status,
                    run.embedding_model,
                    run.embedding_revision,
                    run.embedding_dim,
                ),
            )
            conn.commit()

    def finish_embed_run(self, run: EmbedRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE embed_runs SET
                    finished_at = %s,
                    status = %s,
                    rows_encoded = %s,
                    rows_skipped = %s,
                    error_message = %s
                WHERE id = %s
                """,
                (
                    run.finished_at,
                    run.status,
                    run.rows_encoded,
                    run.rows_skipped,
                    run.error_message,
                    str(run.id),
                ),
            )
            conn.commit()

    def start_cluster_run(self, run: ClusterRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO cluster_runs (
                    id, started_at, status, mode, algorithm, params,
                    embedding_model, embedding_revision, groq_model_light, prompt_version,
                    corpus, n_documents, n_clustered, n_noise, n_themes, n_incremental,
                    c_max, s_max, error_message
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    str(run.id),
                    run.started_at,
                    run.status,
                    run.mode,
                    run.algorithm,
                    Json(run.params or {}),
                    run.embedding_model,
                    run.embedding_revision,
                    run.groq_model_light,
                    run.prompt_version,
                    run.corpus,
                    run.n_documents,
                    run.n_clustered,
                    run.n_noise,
                    run.n_themes,
                    run.n_incremental,
                    run.c_max,
                    run.s_max,
                    run.error_message,
                ),
            )
            conn.commit()

    def finish_cluster_run(self, run: ClusterRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE cluster_runs SET
                    finished_at = %s,
                    status = %s,
                    mode = %s,
                    algorithm = %s,
                    params = %s,
                    embedding_model = %s,
                    embedding_revision = %s,
                    groq_model_light = %s,
                    prompt_version = %s,
                    corpus = %s,
                    n_documents = %s,
                    n_clustered = %s,
                    n_noise = %s,
                    n_themes = %s,
                    n_incremental = %s,
                    c_max = %s,
                    s_max = %s,
                    error_message = %s
                WHERE id = %s
                """,
                (
                    run.finished_at,
                    run.status,
                    run.mode,
                    run.algorithm,
                    Json(run.params or {}),
                    run.embedding_model,
                    run.embedding_revision,
                    run.groq_model_light,
                    run.prompt_version,
                    run.corpus,
                    run.n_documents,
                    run.n_clustered,
                    run.n_noise,
                    run.n_themes,
                    run.n_incremental,
                    run.c_max,
                    run.s_max,
                    run.error_message,
                    str(run.id),
                ),
            )
            conn.commit()

    def list_cluster_runs(self) -> list[ClusterRun]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cluster_runs ORDER BY started_at"
            ).fetchall()
        return [_row_to_cluster_run(r) for r in rows]

    def get_cluster_run(self, run_id: UUID) -> ClusterRun | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM cluster_runs WHERE id = %s", (str(run_id),)
            ).fetchone()
        return _row_to_cluster_run(row) if row else None

    def latest_cluster_run(self, *, success_only: bool = True) -> ClusterRun | None:
        sql = "SELECT * FROM cluster_runs"
        if success_only:
            sql += " WHERE status = 'success'"
        sql += " ORDER BY started_at DESC LIMIT 1"
        with self.connect() as conn:
            row = conn.execute(sql).fetchone()
        return _row_to_cluster_run(row) if row else None

    def upsert_theme(self, theme: ThemeRecord) -> None:
        centroid = as_pgvector(theme.centroid) if theme.centroid is not None else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO themes (
                    id, name, description, hypothesis_flag, bookmark_vs_stall,
                    published, label_status, cluster_run_id, centroid, hdbscan_label,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s::vector, %s,
                    COALESCE(%s, now()), COALESCE(%s, now())
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    hypothesis_flag = EXCLUDED.hypothesis_flag,
                    bookmark_vs_stall = EXCLUDED.bookmark_vs_stall,
                    published = EXCLUDED.published,
                    label_status = EXCLUDED.label_status,
                    cluster_run_id = EXCLUDED.cluster_run_id,
                    centroid = EXCLUDED.centroid,
                    hdbscan_label = EXCLUDED.hdbscan_label,
                    updated_at = COALESCE(EXCLUDED.updated_at, now())
                """,
                (
                    str(theme.id),
                    theme.name,
                    theme.description,
                    theme.hypothesis_flag,
                    theme.bookmark_vs_stall,
                    theme.published,
                    theme.label_status,
                    str(theme.cluster_run_id),
                    centroid,
                    theme.hdbscan_label,
                    theme.created_at,
                    theme.updated_at,
                ),
            )
            conn.commit()

    def get_theme(self, theme_id: UUID) -> ThemeRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM themes WHERE id = %s", (str(theme_id),)
            ).fetchone()
        return _row_to_theme(row) if row else None

    def list_themes(self, cluster_run_id: UUID | None = None) -> list[ThemeRecord]:
        sql = "SELECT * FROM themes"
        params: list[Any] = []
        if cluster_run_id is not None:
            sql += " WHERE cluster_run_id = %s"
            params.append(str(cluster_run_id))
        sql += " ORDER BY name"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_theme(r) for r in rows]

    def replace_document_themes(self, cluster_run_id: UUID, rows: list[DocumentTheme]) -> None:
        values = [
            (
                str(row.document_id),
                str(row.theme_id),
                str(row.cluster_run_id),
                row.assignment_confidence,
                row.assignment_method,
            )
            for row in rows
        ]
        sql = """
            INSERT INTO document_themes (
                document_id, theme_id, cluster_run_id,
                assignment_confidence, assignment_method
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (document_id, theme_id, cluster_run_id) DO UPDATE SET
                assignment_confidence = EXCLUDED.assignment_confidence,
                assignment_method = EXCLUDED.assignment_method
        """
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM document_themes WHERE cluster_run_id = %s",
                (str(cluster_run_id),),
            )
            with conn.cursor() as cur:
                for batch in _batched(values):
                    cur.executemany(sql, batch)
            conn.commit()

    def list_document_themes(
        self,
        *,
        cluster_run_id: UUID | None = None,
        theme_id: UUID | None = None,
    ) -> list[DocumentTheme]:
        sql = "SELECT * FROM document_themes WHERE 1=1"
        params: list[Any] = []
        if cluster_run_id is not None:
            sql += " AND cluster_run_id = %s"
            params.append(str(cluster_run_id))
        if theme_id is not None:
            sql += " AND theme_id = %s"
            params.append(str(theme_id))
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_document_theme(r) for r in rows]

    def replace_theme_metrics(
        self, cluster_run_id: UUID, rows: list[ThemeMetricsSnapshot]
    ) -> None:
        values = [
            (
                str(row.id),
                str(row.theme_id),
                str(row.cluster_run_id),
                row.slice_kind,
                Json(row.slice),
                row.period_start,
                row.period_end,
                row.mention_count,
                row.eligible_corpus_count,
                row.share_of_voice,
                row.source_diversity,
                row.independent_source_density,
                row.sentiment_skew,
                row.sentiment_severity,
                row.trend_direction,
                row.segment_concentration,
                row.segment_breadth,
                row.data_confidence,
                row.impact_score,
                Json(row.unavailable_sources),
                row.denominator_definition,
                row.mean_extraction_confidence,
                row.c_max,
                row.s_max,
                row.computed_at,
            )
            for row in rows
        ]
        sql = """
            INSERT INTO theme_metrics (
                id, theme_id, cluster_run_id, slice_kind, slice,
                period_start, period_end, mention_count, eligible_corpus_count,
                share_of_voice, source_diversity, independent_source_density,
                sentiment_skew, sentiment_severity, trend_direction,
                segment_concentration, segment_breadth, data_confidence,
                impact_score, unavailable_sources, denominator_definition,
                mean_extraction_confidence, c_max, s_max, computed_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s
            )
        """
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM theme_metrics WHERE cluster_run_id = %s",
                (str(cluster_run_id),),
            )
            conn.commit()
        for batch in _batched(values, 200):
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, batch)
                conn.commit()

    def list_theme_metrics(
        self,
        *,
        cluster_run_id: UUID | None = None,
        slice_kind: str | None = None,
        published_only: bool = False,
    ) -> list[ThemeMetricsSnapshot]:
        sql = "SELECT m.* FROM theme_metrics m"
        params: list[Any] = []
        if published_only:
            sql += " JOIN themes t ON t.id = m.theme_id AND t.published IS TRUE"
            if cluster_run_id is not None:
                sql += " AND t.cluster_run_id = m.cluster_run_id"
        sql += " WHERE 1=1"
        if cluster_run_id is not None:
            sql += " AND m.cluster_run_id = %s"
            params.append(str(cluster_run_id))
        if slice_kind is not None:
            sql += " AND m.slice_kind = %s"
            params.append(slice_kind)
        sql += " ORDER BY m.impact_score DESC NULLS LAST"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_theme_metrics(r) for r in rows]

    def list_ingest_runs(self, source_type: str | None = None) -> list[IngestRun]:
        sql = "SELECT * FROM ingest_runs"
        params: list[Any] = []
        if source_type:
            sql += " WHERE source_type = %s"
            params.append(source_type)
        sql += " ORDER BY started_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_ingest_run(r) for r in rows]

    def replace_ngrams(self, cluster_run_id: UUID, rows: list[NgramRow]) -> None:
        values = [
            (
                str(row.id),
                str(cluster_run_id),
                row.gram,
                row.n,
                str(row.theme_id) if row.theme_id else None,
                row.category,
                row.sentiment,
                row.count,
                row.computed_at,
            )
            for row in rows
        ]
        sql = """
            INSERT INTO ngrams (
                id, cluster_run_id, gram, n, theme_id, category, sentiment,
                count, computed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM ngrams WHERE cluster_run_id = %s",
                (str(cluster_run_id),),
            )
            conn.commit()
        for batch in _batched(values, 200):
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(sql, batch)
                conn.commit()

    def list_ngrams(
        self,
        *,
        cluster_run_id: UUID | None = None,
        theme_id: UUID | None = None,
        category: str | None = None,
        sentiment: str | None = None,
        n: int | None = None,
        limit: int | None = 50,
    ) -> list[NgramRow]:
        sql = "SELECT * FROM ngrams WHERE 1=1"
        params: list[Any] = []
        if cluster_run_id is not None:
            sql += " AND cluster_run_id = %s"
            params.append(str(cluster_run_id))
        if theme_id is not None:
            sql += " AND theme_id = %s"
            params.append(str(theme_id))
        if category is not None:
            sql += " AND category = %s"
            params.append(category)
        if sentiment is not None:
            sql += " AND sentiment = %s"
            params.append(sentiment)
        if n is not None:
            sql += " AND n = %s"
            params.append(n)
        sql += " ORDER BY count DESC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_ngram(r) for r in rows]

    def insert_chat_session(self, session: ChatSession) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (id, created_at, groq_model, bge_model, filters)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(session.id),
                    session.created_at,
                    session.groq_model,
                    session.bge_model,
                    Json(session.filters),
                ),
            )
            conn.commit()

    def get_chat_session(self, session_id: UUID) -> ChatSession | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE id = %s",
                (str(session_id),),
            ).fetchone()
        return _row_to_chat_session(row) if row else None

    def insert_chat_message(self, message: ChatMessage) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (
                    id, session_id, role, content, citations, metrics_used,
                    tools_used, confidence_band, status, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(message.id),
                    str(message.session_id),
                    message.role,
                    message.content,
                    Json(message.citations) if message.citations is not None else None,
                    Json(message.metrics_used) if message.metrics_used is not None else None,
                    Json(message.tools_used) if message.tools_used is not None else None,
                    message.confidence_band,
                    message.status,
                    message.created_at,
                ),
            )
            conn.commit()

    def list_chat_messages(self, session_id: UUID) -> list[ChatMessage]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at
                """,
                (str(session_id),),
            ).fetchall()
        return [_row_to_chat_message(r) for r in rows]

    def insert_report(self, artifact: ReportArtifact) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO report_artifacts (
                    id, created_at, period_start, period_end, cluster_run_id,
                    previous_cluster_run_id, title, status, path, header, diff,
                    narrative, groq_model, error_message
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    str(artifact.id),
                    artifact.created_at,
                    artifact.period_start,
                    artifact.period_end,
                    str(artifact.cluster_run_id) if artifact.cluster_run_id else None,
                    str(artifact.previous_cluster_run_id)
                    if artifact.previous_cluster_run_id
                    else None,
                    artifact.title,
                    artifact.status,
                    artifact.path,
                    Json(artifact.header),
                    Json(artifact.diff),
                    artifact.narrative,
                    artifact.groq_model,
                    artifact.error_message,
                ),
            )
            conn.commit()

    def list_reports(self) -> list[ReportArtifact]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM report_artifacts ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_report(r) for r in rows]

    def get_report(self, report_id: UUID) -> ReportArtifact | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM report_artifacts WHERE id = %s",
                (str(report_id),),
            ).fetchone()
        return _row_to_report(row) if row else None


def _row_to_envelope(row: dict) -> RawEnvelope:
    rel = row.get("myntra_relevance")
    return RawEnvelope(
        id=as_uuid(row["id"]),
        source_type=SourceType(row["source_type"]),
        source_id=row["source_id"],
        url=row.get("url"),
        fetched_at=coerce_aware(row["fetched_at"]),
        published_at=coerce_aware(row.get("published_at")),
        platform=row.get("platform"),
        raw_text=row.get("raw_text"),
        raw_title=row.get("raw_title"),
        star_rating=row.get("star_rating"),
        parent_context=row.get("parent_context") or {},
        author_hash=row.get("author_hash"),
        payload_uri=row.get("payload_uri"),
        myntra_relevance=MyntraRelevance(rel) if rel else None,
        reject_reason=row.get("reject_reason"),
        content_hash=row.get("content_hash"),
        ingest_run_id=as_uuid(row["ingest_run_id"]) if row.get("ingest_run_id") else None,
        date_anomaly=bool(row.get("date_anomaly")),
    )


def _row_to_normalized(row: dict) -> NormalizedRecord:
    return NormalizedRecord(
        id=as_uuid(row["id"]),
        raw_id=as_uuid(row["raw_id"]),
        text_original=row["text_original"],
        text_en=row.get("text_en"),
        language=row["language"],
        product_category=row["product_category"],
        gender_segment=row["gender_segment"],
        price_tier=row["price_tier"],
        platform_used=row["platform_used"],
        occasion=row["occasion"],
        star_rating=row.get("star_rating"),
        review_date=coerce_aware(row.get("review_date")),
        quality_score=row.get("quality_score"),
        content_hash=row["content_hash"],
        duplicate_of=as_uuid(row["duplicate_of"]) if row.get("duplicate_of") else None,
        eligible=bool(row["eligible"]),
        pii_scrubbed_at=coerce_aware(row["pii_scrubbed_at"]),
        normalize_run_id=as_uuid(row["normalize_run_id"]) if row.get("normalize_run_id") else None,
        intent_mode=row.get("intent_mode"),
    )


def as_pgvector(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in values) + "]"


def parse_vector(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return []
    return [float(part) for part in text.split(",")]


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _extraction_values(record: ExtractionRecord) -> tuple[Any, ...]:
    return (
        str(record.document_id),
        record.content_hash,
        record.prompt_version,
        record.groq_model,
        record.extraction_status,
        record.intent_tag,
        record.intent_mode,
        Json(record.friction_tags),
        Json(record.residual_uncertainties),
        record.comparison_behavior,
        Json(record.off_platform_info_seeking),
        Json(record.entities),
        record.sentiment_primary,
        record.sentiment_severity,
        Json(record.verbatim_quotes),
        Json(record.maps_to_questions),
        record.extraction_confidence,
        record.raw_response,
        record.error_message,
        record.retry_count,
        record.prompt_tokens,
        record.completion_tokens,
        record.extracted_at,
    )


def _row_to_extraction(row: dict) -> ExtractionRecord:
    return ExtractionRecord(
        document_id=as_uuid(row["document_id"]),
        content_hash=row["content_hash"],
        prompt_version=row["prompt_version"],
        extraction_status=row["extraction_status"],
        groq_model=row.get("groq_model"),
        intent_tag=row.get("intent_tag"),
        intent_mode=row.get("intent_mode"),
        friction_tags=_as_str_list(row.get("friction_tags")),
        residual_uncertainties=_as_str_list(row.get("residual_uncertainties")),
        comparison_behavior=row.get("comparison_behavior"),
        off_platform_info_seeking=_as_str_list(row.get("off_platform_info_seeking")),
        entities=_as_dict(row.get("entities")),
        sentiment_primary=row.get("sentiment_primary"),
        sentiment_severity=row.get("sentiment_severity"),
        verbatim_quotes=list(row.get("verbatim_quotes") or []),
        maps_to_questions=_as_str_list(row.get("maps_to_questions")),
        extraction_confidence=row.get("extraction_confidence"),
        raw_response=row.get("raw_response"),
        error_message=row.get("error_message"),
        retry_count=int(row.get("retry_count") or 0),
        prompt_tokens=int(row.get("prompt_tokens") or 0),
        completion_tokens=int(row.get("completion_tokens") or 0),
        extracted_at=coerce_aware(row.get("extracted_at")),
        metrics_eligible=bool(row.get("metrics_eligible"))
        if row.get("metrics_eligible") is not None
        else row.get("extraction_status") == "ok",
    )


def _row_to_chunk(row: dict) -> ChunkRecord:
    return ChunkRecord(
        id=as_uuid(row["id"]),
        document_id=as_uuid(row["document_id"]),
        ordinal=int(row["ordinal"]),
        text=row["text"],
        token_count=row.get("token_count"),
        embedding=parse_vector(row.get("embedding")),
        embedding_model=row.get("embedding_model"),
        embedding_revision=row.get("embedding_revision"),
        embedding_dim=row.get("embedding_dim"),
        content_hash=row.get("content_hash"),
        source_type=row.get("source_type"),
        published_at=coerce_aware(row.get("published_at")),
        product_category=row.get("product_category"),
        intent_tag=row.get("intent_tag"),
        intent_mode=row.get("intent_mode"),
        friction_tags=_as_str_list(row.get("friction_tags")),
        sentiment=row.get("sentiment"),
        maps_to_questions=_as_str_list(row.get("maps_to_questions")),
        extraction_status=row.get("extraction_status"),
        similarity=float(row["similarity"]) if row.get("similarity") is not None else None,
    )


def _row_to_cluster_run(row: dict) -> ClusterRun:
    params = row.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    return ClusterRun(
        id=as_uuid(row["id"]),
        started_at=coerce_aware(row["started_at"]),
        status=row["status"],
        mode=row["mode"],
        algorithm=row["algorithm"],
        finished_at=coerce_aware(row.get("finished_at")),
        params=params,
        embedding_model=row.get("embedding_model"),
        embedding_revision=row.get("embedding_revision"),
        groq_model_light=row.get("groq_model_light"),
        prompt_version=row.get("prompt_version"),
        corpus=row.get("corpus"),
        n_documents=row.get("n_documents"),
        n_clustered=row.get("n_clustered"),
        n_noise=row.get("n_noise"),
        n_themes=row.get("n_themes"),
        n_incremental=row.get("n_incremental"),
        c_max=row.get("c_max"),
        s_max=row.get("s_max"),
        error_message=row.get("error_message"),
    )


def _row_to_theme(row: dict) -> ThemeRecord:
    return ThemeRecord(
        id=as_uuid(row["id"]),
        name=row["name"],
        cluster_run_id=as_uuid(row["cluster_run_id"]),
        description=row.get("description"),
        hypothesis_flag=bool(row.get("hypothesis_flag")),
        bookmark_vs_stall=row.get("bookmark_vs_stall") or "unclear",
        published=bool(row.get("published")),
        label_status=row.get("label_status") or "pending",
        centroid=parse_vector(row.get("centroid")),
        hdbscan_label=row.get("hdbscan_label"),
        created_at=coerce_aware(row.get("created_at")),
        updated_at=coerce_aware(row.get("updated_at")),
    )


def _row_to_document_theme(row: dict) -> DocumentTheme:
    return DocumentTheme(
        document_id=as_uuid(row["document_id"]),
        theme_id=as_uuid(row["theme_id"]),
        cluster_run_id=as_uuid(row["cluster_run_id"]),
        assignment_method=row["assignment_method"],
        assignment_confidence=row.get("assignment_confidence"),
    )


def _row_to_theme_metrics(row: dict) -> ThemeMetricsSnapshot:
    slice_payload = row.get("slice") or {}
    if not isinstance(slice_payload, dict):
        slice_payload = {}
    unavailable = row.get("unavailable_sources") or []
    if not isinstance(unavailable, list):
        unavailable = []
    return ThemeMetricsSnapshot(
        id=as_uuid(row["id"]),
        theme_id=as_uuid(row["theme_id"]),
        cluster_run_id=as_uuid(row["cluster_run_id"]),
        slice_kind=row["slice_kind"],
        slice=slice_payload,
        mention_count=int(row.get("mention_count") or 0),
        eligible_corpus_count=int(row.get("eligible_corpus_count") or 0),
        share_of_voice=float(row.get("share_of_voice") or 0),
        source_diversity=int(row.get("source_diversity") or 0),
        independent_source_density=int(row.get("independent_source_density") or 0),
        denominator_definition=row.get("denominator_definition") or "",
        c_max=int(row.get("c_max") or 200),
        s_max=int(row.get("s_max") or 4),
        period_start=coerce_aware(row.get("period_start")),
        period_end=coerce_aware(row.get("period_end")),
        sentiment_skew=row.get("sentiment_skew"),
        sentiment_severity=row.get("sentiment_severity"),
        trend_direction=row.get("trend_direction"),
        segment_concentration=row.get("segment_concentration"),
        segment_breadth=row.get("segment_breadth"),
        data_confidence=row.get("data_confidence"),
        impact_score=row.get("impact_score"),
        unavailable_sources=[str(item) for item in unavailable],
        mean_extraction_confidence=row.get("mean_extraction_confidence"),
        computed_at=coerce_aware(row.get("computed_at")),
    )


def _row_to_ingest_run(row: dict) -> IngestRun:
    return IngestRun(
        id=as_uuid(row["id"]),
        source_type=row["source_type"],
        status=row["status"],
        started_at=coerce_aware(row["started_at"]),
        finished_at=coerce_aware(row.get("finished_at")),
        rows_fetched=row.get("rows_fetched"),
        rows_upserted=row.get("rows_upserted"),
        watermark_before=coerce_aware(row.get("watermark_before")),
        watermark_after=coerce_aware(row.get("watermark_after")),
        error_message=row.get("error_message"),
        source_available=row.get("source_available"),
        payload_warning=row.get("payload_warning"),
    )


def _row_to_ngram(row: dict) -> NgramRow:
    return NgramRow(
        id=as_uuid(row["id"]),
        gram=row["gram"],
        n=int(row["n"]),
        count=int(row.get("count") or 0),
        cluster_run_id=as_uuid(row["cluster_run_id"]) if row.get("cluster_run_id") else None,
        theme_id=as_uuid(row["theme_id"]) if row.get("theme_id") else None,
        category=row.get("category"),
        sentiment=row.get("sentiment"),
        computed_at=coerce_aware(row.get("computed_at")),
    )


def _row_to_chat_session(row: dict) -> ChatSession:
    filters = row.get("filters") or {}
    if not isinstance(filters, dict):
        filters = {}
    return ChatSession(
        id=as_uuid(row["id"]),
        created_at=coerce_aware(row["created_at"]),
        groq_model=row.get("groq_model"),
        bge_model=row.get("bge_model"),
        filters=filters,
    )


def _row_to_chat_message(row: dict) -> ChatMessage:
    citations = row.get("citations")
    metrics_used = row.get("metrics_used")
    tools_used = row.get("tools_used")
    return ChatMessage(
        id=as_uuid(row["id"]),
        session_id=as_uuid(row["session_id"]),
        role=row["role"],
        content=row["content"],
        created_at=coerce_aware(row["created_at"]),
        citations=list(citations) if isinstance(citations, list) else None,
        metrics_used=list(metrics_used) if isinstance(metrics_used, list) else None,
        tools_used=list(tools_used) if isinstance(tools_used, list) else None,
        confidence_band=row.get("confidence_band"),
        status=row.get("status"),
    )


def _row_to_report(row: dict) -> ReportArtifact:
    header = row.get("header") or {}
    diff = row.get("diff") or {}
    if not isinstance(header, dict):
        header = {}
    if not isinstance(diff, dict):
        diff = {}
    return ReportArtifact(
        id=as_uuid(row["id"]),
        title=row["title"],
        status=row["status"],
        created_at=coerce_aware(row["created_at"]),
        period_start=coerce_aware(row.get("period_start")),
        period_end=coerce_aware(row.get("period_end")),
        cluster_run_id=as_uuid(row["cluster_run_id"]) if row.get("cluster_run_id") else None,
        previous_cluster_run_id=as_uuid(row["previous_cluster_run_id"])
        if row.get("previous_cluster_run_id")
        else None,
        path=row.get("path"),
        header=header,
        diff=diff,
        narrative=row.get("narrative"),
        groq_model=row.get("groq_model"),
        error_message=row.get("error_message"),
    )
