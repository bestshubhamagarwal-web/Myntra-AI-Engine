"""Query API service: theme_metrics snapshots + evidence joins. No UI math."""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any
from pathlib import Path
from uuid import UUID

from src.api.filters import GlobalFilters, resolve_metrics_slice
from src.config import Settings, load_settings
from src.db.repository import (
    DocumentRepository,
    ExtractionRecord,
    NormalizedRecord,
    ThemeMetricsSnapshot,
    ThemeRecord,
)
from src.metrics.formulas import (
    DENOMINATOR_DEFINITION,
    confidence_band,
    share_of_voice,
    unavailable_source_types,
)
from src.metrics.formulas import iso_week_bucket
from src.models.envelope import SourceType
from src.normalize.category import infer_price_tier
from src.normalize.pii import scrub_pii
from src.reports.pdf import write_report_pdf
from src.timeutil import coerce_aware, utcnow

SEGMENT_DIMENSIONS = (
    "product_category",
    "source_type",
    "gender_segment",
    "price_tier",
    "platform_used",
)

INTENT_ALIASES: dict[str, set[str]] = {
    "bookmark": {"bookmark", "passive_bookmark"},
    "passive_bookmark": {"bookmark", "passive_bookmark"},
    "stall": {"stall", "near_term_purchase"},
    "near_term_purchase": {"stall", "near_term_purchase"},
    "unclear": {"unclear", "unknown", "mixed"},
    "unknown": {"unclear", "unknown", "mixed"},
    "mixed": {"mixed", "unclear"},
}


def _source_value(raw) -> str:
    if raw is None:
        return "unknown"
    value = raw.source_type
    if isinstance(value, SourceType):
        return value.value
    return str(value or "unknown")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    aware = coerce_aware(dt)
    return aware.isoformat() if aware else None


def _date_only(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    aware = coerce_aware(dt)
    return aware.date().isoformat() if aware else None


def _doc_ts(rec: NormalizedRecord, raw) -> datetime | None:
    return coerce_aware(rec.review_date) or (coerce_aware(raw.published_at) if raw else None)


def _effective_price_tier(rec: NormalizedRecord) -> str:
    stored = (rec.price_tier or "unknown").strip() or "unknown"
    if stored != "unknown":
        return stored
    return infer_price_tier(rec.text_original) or "unknown"


def _date_to_exclusive(dt: datetime) -> datetime:
    """Date-only `date_to` includes that calendar day."""
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt + timedelta(days=1)
    return dt


def _filters_need_scan(filters: GlobalFilters) -> bool:
    payload = filters.as_dict()
    return any(value for value in payload.values())


class QueryService:
    """Single metrics path for dashboard and Copilot tools."""

    def __init__(
        self,
        repo: DocumentRepository,
        settings: Settings | None = None,
    ) -> None:
        self.repo = repo
        self.settings = settings or load_settings()

    def _cluster_run(self):
        return self.repo.latest_cluster_run(success_only=True)

    def _unavailable(self) -> list[str]:
        return unavailable_source_types(self.repo.list_source_status(), dashboard=True)

    def _last_successful_pull(self, source_type: str) -> str | None:
        for run in self.repo.list_ingest_runs(source_type):
            if run.status == "success":
                return _iso(run.finished_at or run.started_at)
        return None

    def _extraction_map(self) -> dict[UUID, ExtractionRecord]:
        try:
            rows = self.repo.list_extractions(copy=False)  # type: ignore[call-arg]
        except TypeError:
            rows = self.repo.list_extractions()
        return {row.document_id: row for row in rows}

    def _list_normalized_for_scan(self) -> list[NormalizedRecord]:
        try:
            return self.repo.list_normalized(limit=None, eligible_only=True, copy=False)  # type: ignore[call-arg]
        except TypeError:
            return self.repo.list_normalized(limit=None, eligible_only=True)

    def _raw_by_id(self, docs: list[NormalizedRecord]) -> dict[UUID, Any]:
        stored = getattr(self.repo, "raw", None)
        if isinstance(stored, dict):
            return stored
        needed = {rec.raw_id for rec in docs}
        out: dict[UUID, Any] = {}
        for env in self.repo.list_raw():
            if env.id in needed:
                out[env.id] = env
            if len(out) >= len(needed):
                break
        return out

    def _theme_member_ids(self, theme_id: UUID | None) -> set[UUID] | None:
        if theme_id is None:
            return None
        run = self._cluster_run()
        if run is None:
            return set()
        return {
            row.document_id
            for row in self.repo.list_document_themes(
                cluster_run_id=run.id, theme_id=theme_id
            )
        }

    def _matches(
        self,
        rec: NormalizedRecord,
        raw,
        filters: GlobalFilters,
        *,
        extraction: ExtractionRecord | None = None,
        theme_members: set[UUID] | None = None,
    ) -> bool:
        if not rec.eligible or rec.duplicate_of is not None:
            return False
        if theme_members is not None and rec.id not in theme_members:
            return False
        if filters.source_type and _source_value(raw) != filters.source_type:
            return False
        if filters.product_category and (rec.product_category or "unknown") != filters.product_category:
            return False
        if filters.gender_segment and (rec.gender_segment or "unknown") != filters.gender_segment:
            return False
        if filters.price_tier and _effective_price_tier(rec) != filters.price_tier:
            return False
        if filters.platform_used and (rec.platform_used or "unknown") != filters.platform_used:
            return False
        if filters.intent_mode:
            mode = rec.intent_mode
            if extraction and extraction.intent_mode:
                mode = extraction.intent_mode
            wanted = INTENT_ALIASES.get(filters.intent_mode, {filters.intent_mode})
            if (mode or "unknown") not in wanted:
                return False
        ts = _doc_ts(rec, raw)
        if filters.date_from and (ts is None or ts < filters.date_from):
            return False
        if filters.date_to:
            end = _date_to_exclusive(filters.date_to)
            if ts is None or ts >= end:
                return False
        if filters.friction_tag or filters.intent_tag:
            if extraction is None:
                return False
            if filters.friction_tag and filters.friction_tag not in (extraction.friction_tags or []):
                return False
            if filters.intent_tag and extraction.intent_tag != filters.intent_tag:
                return False
        return True

    def matching_docs(self, filters: GlobalFilters) -> list[tuple[NormalizedRecord, Any]]:
        docs = self._list_normalized_for_scan()
        raw_by_id = self._raw_by_id(docs)
        need_extract = bool(
            filters.intent_mode or filters.friction_tag or filters.intent_tag
        )
        extractions = self._extraction_map() if need_extract else {}
        theme_members = self._theme_member_ids(filters.theme_id)
        rows: list[tuple[NormalizedRecord, Any]] = []
        for rec in docs:
            raw = raw_by_id.get(rec.raw_id)
            if raw is None:
                raw = self.repo.get_raw(rec.raw_id)
            extraction = extractions.get(rec.id) if extractions else None
            if extraction is None and need_extract:
                extraction = self.repo.get_extraction(rec.id)
            if self._matches(
                rec,
                raw,
                filters,
                extraction=extraction,
                theme_members=theme_members,
            ):
                rows.append((rec, raw))
        return rows

    def matching_ids(self, filters: GlobalFilters) -> set[UUID]:
        return {rec.id for rec, _raw in self.matching_docs(filters)}

    def _snapshot_matches_slice(
        self,
        row: ThemeMetricsSnapshot,
        slice_kind: str,
        payload: dict[str, str],
    ) -> bool:
        if row.slice_kind != slice_kind:
            return False
        if slice_kind == "global":
            return True
        if slice_kind == "product_category":
            return row.slice.get("product_category") == payload.get("product_category")
        if slice_kind == "source_type":
            return row.slice.get("source_type") == payload.get("source_type")
        return False

    def overview(self, filters: GlobalFilters) -> dict[str, Any]:
        run = self._cluster_run()
        matched = self.matching_docs(filters)
        extractions = self._extraction_map()
        statuses = self.repo.list_source_status()
        unavailable = unavailable_source_types(statuses, dashboard=True)
        eligible_by_source: Counter[str] = Counter()
        histogram: Counter[str] = Counter()
        intent_tags: Counter[str] = Counter()
        intent_modes: Counter[str] = Counter()
        for rec, raw in matched:
            eligible_by_source[_source_value(raw)] += 1
            bucket = iso_week_bucket(_doc_ts(rec, raw))
            if bucket:
                histogram[bucket] += 1
            extraction = extractions.get(rec.id)
            if extraction:
                intent_tags[extraction.intent_tag or "unknown"] += 1
                intent_modes[extraction.intent_mode or rec.intent_mode or "unknown"] += 1
            else:
                intent_modes[rec.intent_mode or "unknown"] += 1

        counts_by_source = []
        included: list[str] = []
        for status in statuses:
            live = status.status == "live"
            eligible_n = int(eligible_by_source.get(status.source_type, 0))
            if live:
                included.append(status.source_type)
            if not live and status.raw_count <= 0 and eligible_n <= 0:
                continue
            counts_by_source.append(
                {
                    "source_type": status.source_type,
                    "status": status.status,
                    "enabled": status.enabled,
                    "raw_count": status.raw_count,
                    "normalized_count": status.normalized_count,
                    "eligible_count": eligible_n,
                    "volume_is_current": live,
                    "last_run_status": status.last_run_status,
                    "last_successful_pull": self._last_successful_pull(status.source_type),
                    "notes": status.notes,
                }
            )

        last_ingest = None
        all_runs = self.repo.list_ingest_runs()
        if all_runs:
            latest = all_runs[0]
            last_ingest = {
                "id": str(latest.id),
                "source_type": latest.source_type,
                "status": latest.status,
                "finished_at": _iso(latest.finished_at or latest.started_at),
                "source_available": latest.source_available,
            }

        empty = len(matched) == 0
        return {
            "cluster_run_id": str(run.id) if run else None,
            "themes_refreshed_at": _iso(run.started_at) if run else None,
            "corpus": run.corpus if run else None,
            "denominator_definition": DENOMINATOR_DEFINITION,
            "eligible_corpus_count": len(matched),
            "normalized_count": self.repo.count_normalized(),
            "raw_count": self.repo.count_raw(),
            "counts_by_source": counts_by_source,
            "unavailable_sources": unavailable,
            "included_sources": included,
            "date_histogram": [
                {"bucket": key, "count": histogram[key]} for key in sorted(histogram)
            ],
            "intent_tag_counts": dict(intent_tags),
            "intent_mode_counts": dict(intent_modes),
            "last_ingest": last_ingest,
            "filters": filters.as_dict(),
            "empty": empty,
        }

    def themes(self, filters: GlobalFilters) -> dict[str, Any]:
        run = self._cluster_run()
        unavailable = self._unavailable()
        slice_kind, payload = resolve_metrics_slice(filters)
        if run is None:
            return {
                "cluster_run_id": None,
                "themes_refreshed_at": None,
                "denominator_definition": DENOMINATOR_DEFINITION,
                "unavailable_sources": unavailable,
                "metrics_slice": payload,
                "filters": filters.as_dict(),
                "themes": [],
                "empty": True,
            }
        if _filters_need_scan(filters):
            matched_ids = self.matching_ids(filters)
            empty_corpus = len(matched_ids) == 0
        else:
            matched_ids = None
            empty_corpus = False
        theme_rows = {t.id: t for t in self.repo.list_themes(run.id) if t.published}
        assignments = self.repo.list_document_themes(cluster_run_id=run.id)
        members: dict[UUID, set[UUID]] = defaultdict(set)
        for row in assignments:
            members[row.theme_id].add(row.document_id)
        snapshots = [
            row
            for row in self.repo.list_theme_metrics(
                cluster_run_id=run.id, slice_kind=slice_kind, published_only=True
            )
            if self._snapshot_matches_slice(row, slice_kind, payload)
        ]
        time_slices = self.repo.list_theme_metrics(
            cluster_run_id=run.id, slice_kind="time_bucket", published_only=True
        )
        spark_by_theme: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        for row in time_slices:
            bucket = row.slice.get("bucket")
            if not bucket:
                continue
            if filters.date_from or filters.date_to:
                continue
            spark_by_theme[row.theme_id].append(
                {
                    "bucket": bucket,
                    "mention_count": row.mention_count,
                    "share_of_voice": row.share_of_voice,
                }
            )
        cards: list[dict[str, Any]] = []
        if not empty_corpus:
            for snap in snapshots:
                theme = theme_rows.get(snap.theme_id)
                if theme is None:
                    continue
                assigned = members.get(theme.id, set())
                filtered_n = len(assigned) if matched_ids is None else len(assigned & matched_ids)
                if filtered_n <= 0:
                    continue
                spark = sorted(spark_by_theme.get(theme.id, []), key=lambda p: p["bucket"])
                cards.append(
                    self._theme_card(
                        theme,
                        snap,
                        rank=0,
                        spark=spark,
                        evidence_count=len(assigned),
                        filtered_evidence_count=filtered_n,
                        refreshed=_iso(run.started_at),
                    )
                )
        cards.sort(key=lambda c: (c.get("impact_score") or 0.0), reverse=True)
        for index, card in enumerate(cards, start=1):
            card["rank"] = index
        return {
            "cluster_run_id": str(run.id),
            "themes_refreshed_at": _iso(run.started_at),
            "denominator_definition": DENOMINATOR_DEFINITION,
            "unavailable_sources": unavailable,
            "metrics_slice": payload,
            "filters": filters.as_dict(),
            "themes": cards,
            "empty": empty_corpus or not cards,
        }

    def _theme_card(
        self,
        theme: ThemeRecord,
        snap: ThemeMetricsSnapshot,
        *,
        rank: int,
        spark: list[dict[str, Any]],
        evidence_count: int,
        filtered_evidence_count: int,
        refreshed: str | None,
    ) -> dict[str, Any]:
        return {
            "theme_id": str(theme.id),
            "name": theme.name,
            "description": theme.description,
            "rank": rank,
            "mention_count": snap.mention_count,
            "share_of_voice": snap.share_of_voice,
            "data_confidence": snap.data_confidence,
            "confidence_band": confidence_band(snap.data_confidence),
            "sentiment_severity": snap.sentiment_severity,
            "sentiment_skew": snap.sentiment_skew,
            "impact_score": snap.impact_score,
            "source_diversity": snap.source_diversity,
            "independent_source_density": snap.independent_source_density,
            "trend_direction": snap.trend_direction,
            "segment_concentration": snap.segment_concentration,
            "segment_breadth": snap.segment_breadth,
            "unavailable_sources": list(snap.unavailable_sources),
            "eligible_corpus_count": snap.eligible_corpus_count,
            "denominator_definition": snap.denominator_definition,
            "hypothesis_flag": theme.hypothesis_flag,
            "bookmark_vs_stall": theme.bookmark_vs_stall,
            "slice_kind": snap.slice_kind,
            "slice": snap.slice,
            "sparkline": spark,
            "sparkline_insufficient": len(spark) < 2,
            "evidence_count": evidence_count,
            "filtered_evidence_count": filtered_evidence_count,
            "cluster_run_id": str(theme.cluster_run_id),
            "themes_refreshed_at": refreshed,
        }

    def segments(self, filters: GlobalFilters, *, dimension: str = "product_category") -> dict[str, Any]:
        if dimension not in SEGMENT_DIMENSIONS:
            dimension = "product_category"
        run = self._cluster_run()
        unavailable = self._unavailable()
        threshold = self.settings.small_n_threshold
        matched = self.matching_docs(filters)
        matched_ids = {rec.id for rec, _ in matched}
        empty = len(matched_ids) == 0
        cells: list[dict[str, Any]] = []
        if run is None or empty:
            return {
                "dimension": dimension,
                "unknown_visible": True,
                "small_n_threshold": threshold,
                "filters": filters.as_dict(),
                "unavailable_sources": unavailable,
                "cells": [],
                "empty": True,
            }
        themes = {t.id: t for t in self.repo.list_themes(run.id) if t.published}
        if dimension in {"product_category", "source_type"}:
            slices = self.repo.list_theme_metrics(
                cluster_run_id=run.id, slice_kind=dimension, published_only=True
            )
            assignments = self.repo.list_document_themes(cluster_run_id=run.id)
            members: dict[UUID, set[UUID]] = defaultdict(set)
            for row in assignments:
                members[row.theme_id].add(row.document_id)
            for snap in slices:
                theme = themes.get(snap.theme_id)
                if theme is None:
                    continue
                if not (members.get(theme.id, set()) & matched_ids):
                    continue
                segment = str(snap.slice.get(dimension) or "unknown")
                if filters.product_category and dimension == "product_category":
                    if segment != filters.product_category:
                        continue
                if filters.source_type and dimension == "source_type":
                    if segment != filters.source_type:
                        continue
                small = snap.mention_count < threshold
                cells.append(
                    {
                        "theme_id": str(theme.id),
                        "theme_name": theme.name,
                        "dimension": dimension,
                        "segment": segment,
                        "mention_count": snap.mention_count,
                        "eligible_corpus_count": snap.eligible_corpus_count,
                        "share_of_voice": snap.share_of_voice,
                        "data_confidence": snap.data_confidence,
                        "impact_score": snap.impact_score,
                        "unavailable_sources": list(snap.unavailable_sources),
                        "small_n": small,
                        "caveat": (
                            "small_n; do not treat this cell as a majority of users"
                            if small
                            else None
                        ),
                        "from_snapshot": True,
                    }
                )
        else:
            eligible_by_seg: Counter[str] = Counter()
            mention: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
            rec_by_id = {rec.id: rec for rec, _ in matched}
            for rec, _raw in matched:
                if dimension == "price_tier":
                    eligible_by_seg[_effective_price_tier(rec)] += 1
                else:
                    eligible_by_seg[getattr(rec, dimension) or "unknown"] += 1
            for row in self.repo.list_document_themes(cluster_run_id=run.id):
                rec = rec_by_id.get(row.document_id)
                if rec is None:
                    continue
                if dimension == "price_tier":
                    segment = _effective_price_tier(rec)
                else:
                    segment = getattr(rec, dimension) or "unknown"
                mention[(row.theme_id, segment)].add(row.document_id)
            segments = sorted(set(eligible_by_seg) | {"unknown"})
            for theme_id, theme in themes.items():
                for segment in segments:
                    n = len(mention.get((theme_id, segment), set()))
                    denom = int(eligible_by_seg.get(segment, 0))
                    small = n < threshold
                    cells.append(
                        {
                            "theme_id": str(theme_id),
                            "theme_name": theme.name,
                            "dimension": dimension,
                            "segment": segment,
                            "mention_count": n,
                            "eligible_corpus_count": denom,
                            "share_of_voice": share_of_voice(n, denom),
                            "data_confidence": None,
                            "impact_score": None,
                            "unavailable_sources": unavailable,
                            "small_n": small,
                            "caveat": (
                                "small_n; do not treat this cell as a majority of users"
                                if small
                                else None
                            ),
                            "from_snapshot": False,
                        }
                    )
        return {
            "dimension": dimension,
            "unknown_visible": True,
            "small_n_threshold": threshold,
            "filters": filters.as_dict(),
            "unavailable_sources": unavailable,
            "cells": cells,
            "empty": empty or not cells,
        }

    def trends(self, filters: GlobalFilters) -> dict[str, Any]:
        run = self._cluster_run()
        unavailable = self._unavailable()
        matched_ids = self.matching_ids(filters)
        if run is None or not matched_ids:
            return {
                "filters": filters.as_dict(),
                "unavailable_sources": unavailable,
                "series": [],
                "empty": True,
            }
        themes = {t.id: t for t in self.repo.list_themes(run.id) if t.published}
        points = self.repo.list_theme_metrics(
            cluster_run_id=run.id, slice_kind="time_bucket", published_only=True
        )
        by_theme: dict[UUID, list[ThemeMetricsSnapshot]] = defaultdict(list)
        for row in points:
            by_theme[row.theme_id].append(row)
        series: list[dict[str, Any]] = []
        assignments = self.repo.list_document_themes(cluster_run_id=run.id)
        members: dict[UUID, set[UUID]] = defaultdict(set)
        for row in assignments:
            members[row.theme_id].add(row.document_id)
        for theme_id, rows in by_theme.items():
            theme = themes.get(theme_id)
            if theme is None:
                continue
            if not (members.get(theme_id, set()) & matched_ids):
                continue
            ordered = sorted(rows, key=lambda r: str(r.slice.get("bucket") or ""))
            insufficient = len(ordered) < 2
            for row in ordered:
                bucket = str(row.slice.get("bucket") or "")
                if not bucket:
                    continue
                series.append(
                    {
                        "theme_id": str(theme_id),
                        "theme_name": theme.name,
                        "bucket": bucket,
                        "mention_count": row.mention_count,
                        "share_of_voice": row.share_of_voice,
                        "insufficient_history": insufficient,
                    }
                )
        return {
            "filters": filters.as_dict(),
            "unavailable_sources": unavailable,
            "series": series,
            "empty": not series,
        }

    def ngrams(self, filters: GlobalFilters, *, n: int | None = None, limit: int = 50) -> dict[str, Any]:
        run = self._cluster_run()
        cloud_eligible = filters.has_theme_or_category()
        if run is None:
            return {
                "filters": filters.as_dict(),
                "cloud_eligible": cloud_eligible,
                "rows": [],
                "empty": True,
            }
        rows = self.repo.list_ngrams(
            cluster_run_id=run.id,
            theme_id=filters.theme_id,
            category=filters.product_category,
            n=n,
            limit=limit,
        )
        out = [
            {
                "gram": row.gram,
                "n": row.n,
                "count": row.count,
                "theme_id": str(row.theme_id) if row.theme_id else None,
                "category": row.category,
                "sentiment": row.sentiment,
            }
            for row in rows
        ]
        return {
            "filters": filters.as_dict(),
            "cloud_eligible": cloud_eligible,
            "rows": out,
            "empty": not out,
        }

    def evidence(self, filters: GlobalFilters, *, limit: int = 200) -> dict[str, Any]:
        run = self._cluster_run()
        matched = self.matching_docs(filters)
        matched_ids = {rec.id for rec, _ in matched}
        if not matched_ids:
            return {"filters": filters.as_dict(), "rows": [], "empty": True}
        extractions = self._extraction_map()
        theme_names: dict[UUID, str] = {}
        assignments: list[Any] = []
        if run is not None:
            theme_names = {t.id: t.name for t in self.repo.list_themes(run.id) if t.published}
            assignments = self.repo.list_document_themes(
                cluster_run_id=run.id, theme_id=filters.theme_id
            )
        assigned_docs = {row.document_id: row for row in assignments}
        rows_out: list[dict[str, Any]] = []
        seen: set[tuple[UUID, UUID | None, str]] = set()
        for rec, raw in matched:
            if filters.theme_id and rec.id not in assigned_docs:
                continue
            extraction = extractions.get(rec.id)
            quotes = _quotes_for(rec, extraction)
            assignment = assigned_docs.get(rec.id)
            theme_id = assignment.theme_id if assignment else None
            if filters.theme_id and theme_id != filters.theme_id:
                continue
            try:
                chunks = self.repo.list_chunks(rec.id, copy=False)  # type: ignore[call-arg]
            except TypeError:
                chunks = self.repo.list_chunks(rec.id)
            chunk_id = str(chunks[0].id) if chunks else None
            url = raw.url if raw else None
            link_unavailable = not bool(url and str(url).strip())
            source_type = _source_value(raw)
            published = _date_only(_doc_ts(rec, raw))
            for quote in quotes:
                if filters.q and filters.q.lower() not in quote.lower():
                    continue
                key = (rec.id, theme_id, quote)
                if key in seen:
                    continue
                seen.add(key)
                rows_out.append(
                    {
                        "document_id": str(rec.id),
                        "chunk_id": chunk_id,
                        "theme_id": str(theme_id) if theme_id else None,
                        "theme_name": theme_names.get(theme_id) if theme_id else None,
                        "quote": scrub_pii(quote),
                        "source_type": source_type,
                        "url": url,
                        "link_unavailable": link_unavailable,
                        "published_at": published,
                        "product_category": rec.product_category,
                        "intent_mode": (extraction.intent_mode if extraction else rec.intent_mode),
                        "intent_tag": extraction.intent_tag if extraction else None,
                        "friction_tags": list(extraction.friction_tags) if extraction else [],
                        "sentiment": extraction.sentiment_primary if extraction else None,
                        "maps_to_questions": (
                            list(extraction.maps_to_questions) if extraction else []
                        ),
                    }
                )
                if len(rows_out) >= limit:
                    break
            if len(rows_out) >= limit:
                break
        return {
            "filters": filters.as_dict(),
            "rows": rows_out,
            "empty": not rows_out,
        }

    def evidence_csv(self, filters: GlobalFilters) -> str:
        payload = self.evidence(filters, limit=5000)
        buffer = io.StringIO()
        fields = [
            "document_id",
            "theme_id",
            "theme_name",
            "source_type",
            "published_at",
            "product_category",
            "intent_mode",
            "intent_tag",
            "friction_tags",
            "quote",
            "url",
            "link_unavailable",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow(
                {
                    **row,
                    "friction_tags": "|".join(row.get("friction_tags") or []),
                    "quote": scrub_pii(row.get("quote") or ""),
                }
            )
        return buffer.getvalue()

    def reports(self) -> dict[str, Any]:
        items = [self._serialize_report(artifact) for artifact in self.repo.list_reports()]
        if not items:
            live = self._live_report()
            if live:
                items = [live]
        return {"reports": items, "empty": not items}

    def report_detail(self, report_id: UUID) -> dict[str, Any] | None:
        artifact = self.repo.get_report(report_id)
        if artifact is not None:
            item = self._serialize_report(artifact)
            return {
                "id": item["id"],
                "title": item["title"],
                "status": item["status"],
                "header": item["header"],
                "diff": artifact.diff or {"top_themes": item["top_themes"]},
                "narrative": item["narrative"],
                "path": item["path"],
                "top_themes": item["top_themes"],
            }
        live = self._live_report()
        if live and live["id"] == str(report_id):
            return {
                "id": live["id"],
                "title": live["title"],
                "status": live["status"],
                "header": live["header"],
                "diff": {"top_themes": live["top_themes"]},
                "narrative": live["narrative"],
                "path": live["path"],
                "top_themes": live["top_themes"],
            }
        return None

    def resolve_report_pdf(self, report_id: UUID) -> Path | None:
        artifact = self.repo.get_report(report_id)
        candidates: list[Path] = []
        if artifact and artifact.path:
            candidates.append(Path(artifact.path))
        candidates.append(Path(self.settings.reports_path) / f"{report_id}.pdf")
        for path in candidates:
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
        live = self._live_report()
        if live and live["id"] == str(report_id) and live.get("path"):
            path = Path(str(live["path"]))
            if path.is_file():
                return path
        return None

    def _theme_names(self) -> dict[str, str]:
        return {
            str(theme.id): theme.name
            for theme in self.repo.list_themes()
            if theme.published
        }

    def _ranked_theme_rows(self, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        names = self._theme_names()
        ranked: list[dict[str, Any]] = []
        for row in rows or []:
            ranked.append(
                {
                    "theme_id": row.get("theme_id"),
                    "name": row.get("name") or names.get(str(row.get("theme_id") or ""), "Theme"),
                    "mention_count": row.get("mention_count"),
                    "share_of_voice": row.get("share_of_voice"),
                    "impact_score": row.get("impact_score"),
                }
            )
        if ranked:
            return ranked[:6]
        run = self._cluster_run()
        if run is None:
            return []
        snapshots = self.repo.list_theme_metrics(
            cluster_run_id=run.id, slice_kind="global", published_only=True
        )
        snapshots = sorted(
            snapshots,
            key=lambda snap: (float(snap.impact_score or 0), int(snap.mention_count or 0)),
            reverse=True,
        )
        for snap in snapshots[:6]:
            ranked.append(
                {
                    "theme_id": str(snap.theme_id),
                    "name": names.get(str(snap.theme_id), "Theme"),
                    "mention_count": snap.mention_count,
                    "share_of_voice": snap.share_of_voice,
                    "impact_score": snap.impact_score,
                }
            )
        return ranked

    def _narrative_from_themes(self, top: list[dict[str, Any]], corpus_size: Any) -> str:
        if not top:
            return (
                "No published opportunity areas yet. Run python -m src.cli index "
                "to cluster reviews, then open Reports again."
            )
        names = [str(row.get("name")) for row in top[:4] if row.get("name")]
        size = f"{corpus_size} eligible reviews" if corpus_size not in (None, "") else "the current public review slice"
        lead = f"This snapshot covers {size}."
        if names:
            lead += " Top stated issues: " + ", ".join(names) + "."
        return lead + " These are themes from public comments, not proven checkout drop-off."

    def _serialize_report(self, artifact) -> dict[str, Any]:
        top = self._ranked_theme_rows((artifact.diff or {}).get("top_themes"))
        header = dict(artifact.header or {})
        corpus = header.get("corpus_size")
        narrative = (artifact.narrative or "").strip() or self._narrative_from_themes(top, corpus)
        title = artifact.title or "Weekly Myntra wishlist discovery report"
        if title.lower() in {"report", "untitled"} or (len(title) == 36 and title.count("-") == 4):
            title = "Weekly Myntra wishlist discovery report"
        return {
            "id": str(artifact.id),
            "title": title,
            "status": artifact.status or "success",
            "created_at": _iso(artifact.created_at),
            "path": artifact.path,
            "cluster_run_id": str(artifact.cluster_run_id) if artifact.cluster_run_id else None,
            "period_start": _iso(artifact.period_start),
            "period_end": _iso(artifact.period_end),
            "header": header,
            "narrative": narrative,
            "top_themes": top,
        }

    def _live_report(self) -> dict[str, Any] | None:
        run = self._cluster_run()
        if run is None:
            return None
        top = self._ranked_theme_rows()
        if not top:
            return None
        snapshots = self.repo.list_theme_metrics(
            cluster_run_id=run.id, slice_kind="global", published_only=True
        )
        corpus = snapshots[0].eligible_corpus_count if snapshots else None
        header = {
            "corpus_size": corpus,
            "cluster_run_id": str(run.id),
            "first_week": True,
        }
        narrative = self._narrative_from_themes(top, corpus)
        pdf_path = Path(self.settings.reports_path) / f"{run.id}.pdf"
        try:
            if not pdf_path.is_file():
                write_report_pdf(
                    pdf_path,
                    title="Current opportunity snapshot",
                    header_lines=[
                        f"Corpus (eligible): {corpus}",
                        "Findings are stated user language, not proven causal drop-off.",
                    ],
                    narrative=narrative,
                    top_themes=top,
                    period="Current snapshot",
                )
        except OSError:
            pdf_path = None
        return {
            "id": str(run.id),
            "title": "Current opportunity snapshot",
            "status": "success",
            "created_at": _iso(getattr(run, "started_at", None) or utcnow()),
            "path": str(pdf_path) if pdf_path else None,
            "cluster_run_id": str(run.id),
            "period_start": None,
            "period_end": None,
            "header": header,
            "narrative": narrative,
            "top_themes": top,
        }

    def published_theme_ids(self) -> list[UUID]:
        run = self._cluster_run()
        if run is None:
            return []
        return [t.id for t in self.repo.list_themes(run.id) if t.published]


def _quotes_for(rec: NormalizedRecord, extraction: ExtractionRecord | None) -> list[str]:
    quotes: list[str] = []
    if extraction:
        for item in extraction.verbatim_quotes or []:
            span = item.get("span") or item.get("text")
            if span:
                quotes.append(str(span))
    if not quotes:
        text = (rec.text_original or "").strip()
        if text:
            quotes.append(text[:280])
    return quotes
