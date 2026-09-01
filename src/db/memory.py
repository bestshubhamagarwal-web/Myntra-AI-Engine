from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

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
from src.ingest.allowlist import DEFAULT_SOURCE_NOTES
from src.models.envelope import ARCHITECTURE_SOURCE_TYPES, RawEnvelope
from src.timeutil import coerce_aware, utcnow


class MemoryRepository:
    """In-memory store for Phase 1 unit tests (no Postgres required)."""

    def __init__(self) -> None:
        self.source_config: dict[str, dict[str, Any]] = {
            name: {
                "enabled": name
                in {"play_store", "app_store", "reddit", "youtube", "x"},
                "notes": DEFAULT_SOURCE_NOTES.get(name),
            }
            for name in ARCHITECTURE_SOURCE_TYPES
        }
        self.source_config["instagram"]["enabled"] = False
        self.source_config["facebook"]["enabled"] = False
        self.source_config["quora"]["enabled"] = False
        self.source_config["forum"]["enabled"] = False
        self.source_config["myntra_qa"]["enabled"] = False
        self.source_config["myntra_review"]["enabled"] = False
        self.source_config["other"]["enabled"] = False
        self.ingest_runs: dict[UUID, IngestRun] = {}
        self.raw: dict[UUID, RawEnvelope] = {}
        self.raw_by_natural: dict[tuple[str, str], UUID] = {}
        self.normalized: dict[UUID, NormalizedRecord] = {}
        self.normalized_by_raw: dict[UUID, UUID] = {}
        self.normalized_by_hash: dict[str, UUID] = {}
        self.normalize_runs: dict[UUID, dict[str, Any]] = {}
        self.extractions: dict[UUID, ExtractionRecord] = {}
        self.chunks: dict[UUID, ChunkRecord] = {}
        self.chunks_by_document: dict[UUID, list[UUID]] = {}
        self.extract_runs: dict[UUID, ExtractRun] = {}
        self.embed_runs: dict[UUID, EmbedRun] = {}
        self.cluster_runs: dict[UUID, ClusterRun] = {}
        self.themes: dict[UUID, ThemeRecord] = {}
        self.document_themes: list[DocumentTheme] = []
        self.theme_metrics: list[ThemeMetricsSnapshot] = []
        self.ngrams: list[NgramRow] = []
        self.chat_sessions: dict[UUID, ChatSession] = {}
        self.chat_messages: dict[UUID, list[ChatMessage]] = {}
        self.reports: dict[UUID, ReportArtifact] = {}
        self.ingest_queries = [
            {"query_text": "Myntra wishlist", "source_type": None, "active": True},
            {"query_text": "Myntra cart", "source_type": None, "active": True},
            {"query_text": "Myntra sizing", "source_type": None, "active": True},
            {"query_text": "Myntra returns", "source_type": None, "active": True},
            {"query_text": "Myntra vs AJIO", "source_type": None, "active": True},
            {"query_text": "Myntra haul", "source_type": "youtube", "active": True},
            {"query_text": "Myntra try-on", "source_type": "youtube", "active": True},
            {"query_text": "Myntra size guide", "source_type": "youtube", "active": True},
            {"query_text": "Myntra unboxing", "source_type": "youtube", "active": True},
        ]

    def is_enabled(self, source_type: str) -> bool:
        row = self.source_config.get(source_type)
        if not row:
            return False
        return bool(row["enabled"])

    def set_enabled(self, source_type: str, enabled: bool) -> None:
        self.source_config.setdefault(source_type, {"notes": None})
        self.source_config[source_type]["enabled"] = enabled

    def start_ingest_run(self, run: IngestRun) -> None:
        self.ingest_runs[run.id] = run

    def finish_ingest_run(self, run: IngestRun) -> None:
        self.ingest_runs[run.id] = run

    def get_watermark(self, source_type: str) -> datetime | None:
        now = utcnow()
        times = []
        for env in self.raw.values():
            if env.source_type.value != source_type:
                continue
            if env.date_anomaly or env.published_at is None:
                continue
            published = coerce_aware(env.published_at)
            if published and published <= now:
                times.append(published)
        return max(times) if times else None

    def upsert_raw(self, envelope: RawEnvelope) -> tuple[UUID, bool]:
        key = (envelope.source_type.value, envelope.source_id)
        existing_id = self.raw_by_natural.get(key)
        if existing_id:
            old = self.raw[existing_id]
            envelope.id = existing_id
            envelope.fetched_at = coerce_aware(envelope.fetched_at) or utcnow()
            self.raw[existing_id] = envelope
            return existing_id, False
        self.raw[envelope.id] = envelope
        self.raw_by_natural[key] = envelope.id
        return envelope.id, True

    def get_raw(self, raw_id: UUID) -> RawEnvelope | None:
        env = self.raw.get(raw_id)
        return deepcopy(env) if env else None

    def list_raw(
        self,
        *,
        source_type: str | None = None,
        limit: int | None = None,
    ) -> list[RawEnvelope]:
        rows = list(self.raw.values())
        if source_type:
            rows = [e for e in rows if e.source_type.value == source_type]
        rows.sort(key=lambda e: e.fetched_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        if limit is not None:
            rows = rows[:limit]
        return [deepcopy(e) for e in rows]

    def list_raw_for_run(self, ingest_run_id: UUID) -> list[RawEnvelope]:
        return [deepcopy(e) for e in self.raw.values() if e.ingest_run_id == ingest_run_id]

    def list_raw_pending_normalize(self) -> list[RawEnvelope]:
        out = []
        for env in self.raw.values():
            if env.id in self.normalized_by_raw:
                continue
            if env.myntra_relevance is not None and env.myntra_relevance.value == "reject":
                continue
            if env.reject_reason:
                continue
            out.append(deepcopy(env))
        return out

    def list_stale_raw(self) -> list[RawEnvelope]:
        from src.normalize.text import expected_content_hash

        out = []
        for env in self.raw.values():
            nid = self.normalized_by_raw.get(env.id)
            if not nid:
                continue
            rec = self.normalized[nid]
            if rec.content_hash != expected_content_hash(env):
                out.append(deepcopy(env))
        return out

    def list_raw_rejected(self, limit: int = 20) -> list[RawEnvelope]:
        rows = [
            deepcopy(e)
            for e in self.raw.values()
            if e.myntra_relevance is not None and e.myntra_relevance.value == "reject"
        ]
        return rows[:limit]

    def mark_raw_decision(
        self,
        raw_id: UUID,
        relevance: str,
        reject_reason: str | None,
    ) -> None:
        env = self.raw[raw_id]
        data = env.model_dump()
        data["myntra_relevance"] = relevance
        data["reject_reason"] = reject_reason
        self.raw[raw_id] = RawEnvelope.model_validate(data)

    def start_normalize_run(
        self, run_id: UUID, started_at: datetime, since_ingest_run_id: UUID | None
    ) -> None:
        self.normalize_runs[run_id] = {
            "started_at": started_at,
            "since_ingest_run_id": since_ingest_run_id,
        }

    def finish_normalize_run(
        self,
        run_id: UUID,
        finished_at: datetime,
        rows_accepted: int,
        rows_rejected: int,
        status: str,
    ) -> None:
        self.normalize_runs[run_id].update(
            {
                "finished_at": finished_at,
                "rows_accepted": rows_accepted,
                "rows_rejected": rows_rejected,
                "status": status,
            }
        )

    def find_normalized_by_content_hash(self, content_hash: str) -> UUID | None:
        return self.normalized_by_hash.get(content_hash)

    def get_normalized_by_raw_id(self, raw_id: UUID) -> NormalizedRecord | None:
        nid = self.normalized_by_raw.get(raw_id)
        return deepcopy(self.normalized[nid]) if nid else None

    def upsert_normalized(self, record: NormalizedRecord) -> None:
        existing_nid = self.normalized_by_raw.get(record.raw_id)
        if existing_nid and existing_nid != record.id:
            old = self.normalized.pop(existing_nid, None)
            if old and self.normalized_by_hash.get(old.content_hash) == existing_nid:
                del self.normalized_by_hash[old.content_hash]
        old = self.normalized.get(record.id)
        if old and old.content_hash != record.content_hash:
            if self.normalized_by_hash.get(old.content_hash) == record.id:
                del self.normalized_by_hash[old.content_hash]
        self.normalized[record.id] = record
        self.normalized_by_raw[record.raw_id] = record.id
        if record.duplicate_of is None:
            self.normalized_by_hash[record.content_hash] = record.id
        elif self.normalized_by_hash.get(record.content_hash) == record.id:
            del self.normalized_by_hash[record.content_hash]

    def count_raw(self, source_type: str | None = None) -> int:
        if source_type is None:
            return len(self.raw)
        return sum(1 for e in self.raw.values() if e.source_type.value == source_type)

    def count_normalized(self, source_type: str | None = None) -> int:
        if source_type is None:
            return len(self.normalized)
        n = 0
        for rec in self.normalized.values():
            env = self.raw.get(rec.raw_id)
            if env and env.source_type.value == source_type:
                n += 1
        return n

    def list_source_status(self) -> list[SourceStatus]:
        out = []
        for source_type, cfg in self.source_config.items():
            runs = [r for r in self.ingest_runs.values() if r.source_type == source_type]
            last = max(runs, key=lambda r: r.started_at) if runs else None
            meaningful = [r for r in runs if r.status not in {"skipped_locked", "running"}]
            last_meaningful = (
                max(meaningful, key=lambda r: r.started_at) if meaningful else None
            )
            if not cfg["enabled"]:
                status = "unavailable"
            elif last_meaningful and last_meaningful.status == "failed":
                status = "failed"
            elif last_meaningful and last_meaningful.status in {
                "skipped_disabled",
                "skipped_unconfigured",
            }:
                status = "unavailable"
            elif last_meaningful and last_meaningful.status == "success":
                status = "live"
            else:
                status = "unavailable"
            raw_count = sum(
                1 for e in self.raw.values() if e.source_type.value == source_type
            )
            normalized_count = 0
            for rec in self.normalized.values():
                env = self.raw.get(rec.raw_id)
                if env and env.source_type.value == source_type:
                    normalized_count += 1
            out.append(
                SourceStatus(
                    source_type=source_type,
                    status=status,
                    enabled=bool(cfg["enabled"]),
                    notes=cfg.get("notes"),
                    last_run_id=last.id if last else None,
                    last_run_status=last.status if last else None,
                    last_run_finished_at=last.finished_at if last else None,
                    last_rows_fetched=last.rows_fetched if last else None,
                    last_source_available=last.source_available if last else None,
                    raw_count=raw_count,
                    normalized_count=normalized_count,
                )
            )
        return out

    def list_ingest_queries(self) -> list[dict[str, Any]]:
        return list(self.ingest_queries)

    def list_normalized(
        self,
        limit: int | None = 20,
        *,
        eligible_only: bool = False,
        random_sample: bool = False,
        source_type: str | None = None,
        copy: bool = True,
    ) -> list[NormalizedRecord]:
        rows = list(self.normalized.values())
        if eligible_only:
            rows = [r for r in rows if r.eligible]
        if source_type:
            rows = [
                rec
                for rec in rows
                if (env := self.raw.get(rec.raw_id)) is not None
                and env.source_type.value == source_type
            ]
        if random_sample:
            import random

            random.shuffle(rows)
        if limit is not None:
            rows = rows[:limit]
        return [deepcopy(r) for r in rows] if copy else rows

    def get_normalized(self, document_id: UUID) -> NormalizedRecord | None:
        rec = self.normalized.get(document_id)
        return deepcopy(rec) if rec else None

    def set_normalized_intent_mode(self, document_id: UUID, intent_mode: str | None) -> None:
        rec = self.normalized.get(document_id)
        if rec is None:
            return
        rec.intent_mode = intent_mode

    def list_extract_candidates(
        self,
        *,
        resume_after: UUID | None = None,
        limit: int | None = None,
        retry_failed: bool = True,
    ) -> list[NormalizedRecord]:
        docs = [r for r in self.normalized.values() if r.eligible]
        docs.sort(key=lambda r: r.id)
        out: list[NormalizedRecord] = []
        for rec in docs:
            if resume_after is not None and rec.id <= resume_after:
                continue
            if not (rec.text_original or "").strip():
                continue
            existing = self.extractions.get(rec.id)
            if existing is None or existing.extraction_status == "pending":
                out.append(rec)
            elif existing.content_hash != rec.content_hash:
                out.append(rec)
            elif retry_failed and existing.extraction_status == "failed":
                out.append(rec)
            if limit is not None and len(out) >= limit:
                break
        return [deepcopy(r) for r in out]

    def get_extraction(self, document_id: UUID) -> ExtractionRecord | None:
        rec = self.extractions.get(document_id)
        return deepcopy(rec) if rec else None

    def upsert_extraction(self, record: ExtractionRecord) -> None:
        record.metrics_eligible = record.extraction_status == "ok"
        self.extractions[record.document_id] = deepcopy(record)

    def list_extractions(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        metrics_eligible_only: bool = False,
        copy: bool = True,
    ) -> list[ExtractionRecord]:
        rows = list(self.extractions.values())
        if status:
            rows = [r for r in rows if r.extraction_status == status]
        if metrics_eligible_only:
            rows = [r for r in rows if r.extraction_status == "ok" and r.metrics_eligible]
        rows.sort(key=lambda r: r.document_id)
        if limit is not None:
            rows = rows[:limit]
        return [deepcopy(r) for r in rows] if copy else rows

    def start_extract_run(self, run: ExtractRun) -> None:
        self.extract_runs[run.id] = run

    def finish_extract_run(self, run: ExtractRun) -> None:
        self.extract_runs[run.id] = run

    def list_embed_candidates(self, *, limit: int | None = None) -> list[NormalizedRecord]:
        docs = [
            r
            for r in self.normalized.values()
            if r.eligible and (r.text_original or "").strip()
        ]
        docs.sort(key=lambda r: r.id)
        if limit is not None:
            docs = docs[:limit]
        return [deepcopy(r) for r in docs]

    def list_chunks(self, document_id: UUID | None = None, *, copy: bool = True) -> list[ChunkRecord]:
        if document_id is not None:
            ids = self.chunks_by_document.get(document_id, [])
            rows = [self.chunks[i] for i in ids if i in self.chunks]
        else:
            rows = list(self.chunks.values())
        rows.sort(key=lambda c: (c.document_id, c.ordinal))
        return [deepcopy(c) for c in rows] if copy else rows

    def replace_chunks(self, document_id: UUID, chunks: list[ChunkRecord]) -> None:
        for old_id in list(self.chunks_by_document.get(document_id, [])):
            self.chunks.pop(old_id, None)
        self.chunks_by_document[document_id] = []
        for chunk in chunks:
            stored = deepcopy(chunk)
            self.chunks[stored.id] = stored
            self.chunks_by_document.setdefault(document_id, []).append(stored.id)

    def update_chunk_embedding(
        self,
        chunk_id: UUID,
        embedding: list[float],
        *,
        embedding_model: str,
        embedding_revision: str | None,
        embedding_dim: int,
    ) -> None:
        chunk = self.chunks[chunk_id]
        chunk.embedding = list(embedding)
        chunk.embedding_model = embedding_model
        chunk.embedding_revision = embedding_revision
        chunk.embedding_dim = embedding_dim

    def update_chunk_metadata(self, document_id: UUID, extraction: ExtractionRecord) -> None:
        for chunk_id in self.chunks_by_document.get(document_id, []):
            chunk = self.chunks.get(chunk_id)
            if chunk is None:
                continue
            chunk.intent_tag = extraction.intent_tag
            chunk.intent_mode = extraction.intent_mode
            chunk.friction_tags = list(extraction.friction_tags)
            chunk.sentiment = extraction.sentiment_primary
            chunk.maps_to_questions = list(extraction.maps_to_questions)
            chunk.extraction_status = extraction.extraction_status

    def distinct_embedding_models(self) -> list[str]:
        names = {
            c.embedding_model
            for c in self.chunks.values()
            if c.embedding is not None and c.embedding_model
        }
        return sorted(names)

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
        scored: list[tuple[float, ChunkRecord]] = []
        for chunk in self.chunks.values():
            if chunk.embedding is None:
                continue
            if intent_mode and chunk.intent_mode != intent_mode:
                continue
            if friction_tag and friction_tag not in (chunk.friction_tags or []):
                continue
            if product_category and (chunk.product_category or "unknown") != product_category:
                continue
            if source_type and chunk.source_type != source_type:
                continue
            if maps_to_question and maps_to_question not in (chunk.maps_to_questions or []):
                continue
            published = coerce_aware(chunk.published_at)
            if date_from and (published is None or published < date_from):
                continue
            if date_to and (published is None or published > date_to):
                continue
            sim = _dot(query, chunk.embedding)
            scored.append((sim, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        out = []
        for sim, chunk in scored[:k]:
            copied = deepcopy(chunk)
            copied.similarity = sim
            out.append(copied)
        return out

    def list_ingest_runs(self, source_type: str | None = None) -> list[IngestRun]:
        rows = list(self.ingest_runs.values())
        if source_type:
            rows = [r for r in rows if r.source_type == source_type]
        rows.sort(key=lambda r: r.started_at, reverse=True)
        return [deepcopy(r) for r in rows]

    def replace_ngrams(self, cluster_run_id: UUID, rows: list[NgramRow]) -> None:
        self.ngrams = [r for r in self.ngrams if r.cluster_run_id != cluster_run_id]
        self.ngrams.extend(deepcopy(r) for r in rows)

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
        rows = list(self.ngrams)
        if cluster_run_id is not None:
            rows = [r for r in rows if r.cluster_run_id == cluster_run_id]
        if theme_id is not None:
            rows = [r for r in rows if r.theme_id == theme_id]
        if category is not None:
            rows = [r for r in rows if (r.category or "unknown") == category]
        if sentiment is not None:
            rows = [r for r in rows if r.sentiment == sentiment]
        if n is not None:
            rows = [r for r in rows if r.n == n]
        rows.sort(key=lambda r: r.count, reverse=True)
        if limit is not None:
            rows = rows[:limit]
        return [deepcopy(r) for r in rows]

    def insert_chat_session(self, session: ChatSession) -> None:
        self.chat_sessions[session.id] = deepcopy(session)
        self.chat_messages.setdefault(session.id, [])

    def get_chat_session(self, session_id: UUID) -> ChatSession | None:
        rec = self.chat_sessions.get(session_id)
        return deepcopy(rec) if rec else None

    def insert_chat_message(self, message: ChatMessage) -> None:
        self.chat_messages.setdefault(message.session_id, []).append(deepcopy(message))

    def list_chat_messages(self, session_id: UUID) -> list[ChatMessage]:
        rows = list(self.chat_messages.get(session_id, []))
        rows.sort(key=lambda m: m.created_at)
        return [deepcopy(m) for m in rows]

    def insert_report(self, artifact: ReportArtifact) -> None:
        self.reports[artifact.id] = deepcopy(artifact)

    def list_reports(self) -> list[ReportArtifact]:
        rows = list(self.reports.values())
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return [deepcopy(r) for r in rows]

    def get_report(self, report_id: UUID) -> ReportArtifact | None:
        rec = self.reports.get(report_id)
        return deepcopy(rec) if rec else None

    def start_embed_run(self, run: EmbedRun) -> None:
        self.embed_runs[run.id] = run

    def finish_embed_run(self, run: EmbedRun) -> None:
        self.embed_runs[run.id] = run

    def start_cluster_run(self, run: ClusterRun) -> None:
        self.cluster_runs[run.id] = deepcopy(run)

    def finish_cluster_run(self, run: ClusterRun) -> None:
        self.cluster_runs[run.id] = deepcopy(run)

    def list_cluster_runs(self) -> list[ClusterRun]:
        rows = list(self.cluster_runs.values())
        rows.sort(key=lambda r: r.started_at)
        return [deepcopy(r) for r in rows]

    def get_cluster_run(self, run_id: UUID) -> ClusterRun | None:
        rec = self.cluster_runs.get(run_id)
        return deepcopy(rec) if rec else None

    def latest_cluster_run(self, *, success_only: bool = True) -> ClusterRun | None:
        rows = list(self.cluster_runs.values())
        if success_only:
            rows = [r for r in rows if r.status == "success"]
        if not rows:
            return None
        return deepcopy(max(rows, key=lambda r: r.started_at))

    def upsert_theme(self, theme: ThemeRecord) -> None:
        self.themes[theme.id] = deepcopy(theme)

    def get_theme(self, theme_id: UUID) -> ThemeRecord | None:
        rec = self.themes.get(theme_id)
        return deepcopy(rec) if rec else None

    def list_themes(self, cluster_run_id: UUID | None = None) -> list[ThemeRecord]:
        rows = list(self.themes.values())
        if cluster_run_id is not None:
            rows = [t for t in rows if t.cluster_run_id == cluster_run_id]
        rows.sort(key=lambda t: t.name)
        return [deepcopy(t) for t in rows]

    def replace_document_themes(self, cluster_run_id: UUID, rows: list[DocumentTheme]) -> None:
        self.document_themes = [r for r in self.document_themes if r.cluster_run_id != cluster_run_id]
        self.document_themes.extend(deepcopy(r) for r in rows)

    def list_document_themes(
        self,
        *,
        cluster_run_id: UUID | None = None,
        theme_id: UUID | None = None,
    ) -> list[DocumentTheme]:
        rows = list(self.document_themes)
        if cluster_run_id is not None:
            rows = [r for r in rows if r.cluster_run_id == cluster_run_id]
        if theme_id is not None:
            rows = [r for r in rows if r.theme_id == theme_id]
        return [deepcopy(r) for r in rows]

    def replace_theme_metrics(
        self, cluster_run_id: UUID, rows: list[ThemeMetricsSnapshot]
    ) -> None:
        self.theme_metrics = [r for r in self.theme_metrics if r.cluster_run_id != cluster_run_id]
        self.theme_metrics.extend(deepcopy(r) for r in rows)

    def list_theme_metrics(
        self,
        *,
        cluster_run_id: UUID | None = None,
        slice_kind: str | None = None,
        published_only: bool = False,
    ) -> list[ThemeMetricsSnapshot]:
        rows = list(self.theme_metrics)
        if cluster_run_id is not None:
            rows = [r for r in rows if r.cluster_run_id == cluster_run_id]
        if slice_kind is not None:
            rows = [r for r in rows if r.slice_kind == slice_kind]
        if published_only:
            published = {
                t.id for t in self.themes.values() if t.published and (
                    cluster_run_id is None or t.cluster_run_id == cluster_run_id
                )
            }
            rows = [r for r in rows if r.theme_id in published]
        rows.sort(key=lambda r: (r.impact_score or 0.0), reverse=True)
        return [deepcopy(r) for r in rows]


def _dot(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))
