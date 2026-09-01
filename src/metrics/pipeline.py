"""Theme metric snapshots for global + primary slices (Architecture §8.4–8.6)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from src.config import Settings, load_settings
from src.db.repository import (
    DocumentRepository,
    ExtractionRecord,
    NormalizedRecord,
    ThemeMetricsSnapshot,
)
from src.metrics.formulas import (
    DENOMINATOR_DEFINITION,
    blocking_severity,
    data_confidence,
    impact_score,
    independent_source_density,
    iso_week_bucket,
    quality_weighted_mean,
    segment_breadth,
    segment_concentration,
    share_of_voice,
    trend_direction,
    unavailable_source_types,
)
from src.models.envelope import SourceType
from src.timeutil import utcnow


@dataclass
class CorpusRow:
    document_id: UUID
    source_type: str
    product_category: str
    gender_segment: str
    price_tier: str
    platform_used: str
    author_hash: str | None
    timestamp: datetime | None
    quality_score: float | None
    extraction: ExtractionRecord | None
    normalized: NormalizedRecord


@dataclass
class MetricsBatchResult:
    cluster_run_id: UUID
    n_snapshots: int
    n_themes: int
    status: str = "success"


def _source_type(raw) -> str:
    if raw is None:
        return "unknown"
    value = raw.source_type
    if isinstance(value, SourceType):
        return value.value
    return str(value or "unknown")


def load_eligible_corpus(repo: DocumentRepository) -> list[CorpusRow]:
    rows: list[CorpusRow] = []
    store = getattr(repo, "normalized", None)
    raw_store = getattr(repo, "raw", None)
    ext_store = getattr(repo, "extractions", None)
    if store is not None:
        docs = [d for d in store.values() if d.eligible and d.duplicate_of is None]
    else:
        docs = [r for r in repo.list_normalized(limit=None, eligible_only=True) if r.duplicate_of is None]
    for rec in docs:
        raw = raw_store.get(rec.raw_id) if raw_store is not None else repo.get_raw(rec.raw_id)
        extraction = ext_store.get(rec.id) if ext_store is not None else repo.get_extraction(rec.id)
        rows.append(
            CorpusRow(
                document_id=rec.id,
                source_type=_source_type(raw),
                product_category=rec.product_category or "unknown",
                gender_segment=rec.gender_segment or "unknown",
                price_tier=rec.price_tier or "unknown",
                platform_used=rec.platform_used or "unknown",
                author_hash=raw.author_hash if raw else None,
                timestamp=rec.review_date or (raw.published_at if raw else None),
                quality_score=rec.quality_score,
                extraction=extraction,
                normalized=rec,
            )
        )
    return rows


def _matches(row: CorpusRow, slice_kind: str, slice_payload: dict[str, Any]) -> bool:
    if slice_kind == "global":
        return True
    if slice_kind == "product_category":
        return row.product_category == slice_payload.get("product_category")
    if slice_kind == "source_type":
        return row.source_type == slice_payload.get("source_type")
    if slice_kind == "time_bucket":
        return iso_week_bucket(row.timestamp) == slice_payload.get("bucket")
    return False


def _period(rows: list[CorpusRow]) -> tuple[datetime | None, datetime | None]:
    dates = [row.timestamp for row in rows if row.timestamp is not None]
    if not dates:
        return None, None
    return min(dates), max(dates)


def _category_counts(members: list[CorpusRow]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in members:
        counts[row.product_category or "unknown"] += 1
    return dict(counts)


def _week_series(members: list[CorpusRow]) -> list[tuple[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    for row in members:
        bucket = iso_week_bucket(row.timestamp)
        if bucket:
            counts[bucket] += 1
    return sorted(counts.items())


def snapshot_for_slice(
    *,
    theme_id: UUID,
    cluster_run_id: UUID,
    slice_kind: str,
    slice_payload: dict[str, Any],
    members: list[CorpusRow],
    corpus: list[CorpusRow],
    unavailable: list[str],
    c_max: int,
    s_max: int,
) -> ThemeMetricsSnapshot:
    mention_count = len({row.document_id for row in members})
    eligible_corpus_count = len(corpus)
    sov = share_of_voice(mention_count, eligible_corpus_count)
    source_div = len({row.source_type for row in members if row.source_type})
    density = independent_source_density(
        (row.author_hash for row in members),
        (row.source_type for row in members),
    )
    blocking_pairs = [
        (
            blocking_severity(
                row.extraction.sentiment_primary if row.extraction else None,
                row.extraction.sentiment_severity if row.extraction else None,
            ),
            row.quality_score,
        )
        for row in members
    ]
    severity = quality_weighted_mean(blocking_pairs) if members else 0.0
    cat_counts = _category_counts(members)
    concentration = segment_concentration(cat_counts)
    breadth = segment_breadth(cat_counts)
    confidences = [
        row.extraction.extraction_confidence
        for row in members
        if row.extraction is not None
    ]
    mean_conf = (
        sum(c or 0.0 for c in confidences) / len(confidences) if confidences else 0.0
    )
    confidence = data_confidence(
        mention_count,
        source_div,
        mean_conf,
        c_max=c_max,
        s_max=s_max,
    )
    trend = trend_direction(_week_series(members))
    period_start, period_end = _period(corpus)
    return ThemeMetricsSnapshot(
        id=uuid4(),
        theme_id=theme_id,
        cluster_run_id=cluster_run_id,
        slice_kind=slice_kind,
        slice=slice_payload,
        mention_count=mention_count,
        eligible_corpus_count=eligible_corpus_count,
        share_of_voice=sov,
        source_diversity=source_div,
        independent_source_density=density,
        denominator_definition=DENOMINATOR_DEFINITION,
        c_max=c_max,
        s_max=s_max,
        period_start=period_start,
        period_end=period_end,
        sentiment_skew=severity,
        sentiment_severity=severity,
        trend_direction=trend,
        segment_concentration=concentration,
        segment_breadth=breadth,
        data_confidence=confidence,
        impact_score=impact_score(sov, severity, breadth, confidence),
        unavailable_sources=list(unavailable),
        mean_extraction_confidence=mean_conf,
        computed_at=utcnow(),
    )


def planned_slices(corpus: list[CorpusRow]) -> list[tuple[str, dict[str, Any]]]:
    slices: list[tuple[str, dict[str, Any]]] = [("global", {"kind": "global"})]
    categories = sorted({row.product_category or "unknown" for row in corpus})
    if "unknown" not in categories:
        categories.append("unknown")
    for category in categories:
        slices.append(
            ("product_category", {"kind": "product_category", "product_category": category})
        )
    for source in sorted({row.source_type for row in corpus if row.source_type}):
        slices.append(("source_type", {"kind": "source_type", "source_type": source}))
    for bucket in sorted({iso_week_bucket(row.timestamp) for row in corpus if row.timestamp}):
        if bucket:
            slices.append(("time_bucket", {"kind": "time_bucket", "bucket": bucket}))
    return slices


def run_metrics(
    repo: DocumentRepository,
    settings: Settings | None = None,
    *,
    cluster_run_id: UUID | None = None,
) -> MetricsBatchResult:
    cfg = settings or load_settings()
    run = repo.get_cluster_run(cluster_run_id) if cluster_run_id else repo.latest_cluster_run()
    if run is None:
        raise ValueError("no cluster_run to score; run cluster first")
    run_id = run.id
    themes = [t for t in repo.list_themes(run_id) if t.published]
    assignments = repo.list_document_themes(cluster_run_id=run_id)
    by_theme: dict[UUID, set[UUID]] = defaultdict(set)
    for row in assignments:
        by_theme[row.theme_id].add(row.document_id)

    corpus = load_eligible_corpus(repo)
    corpus_by_id = {row.document_id: row for row in corpus}
    unavailable = unavailable_source_types(repo.list_source_status())
    slices = planned_slices(corpus)
    snapshots: list[ThemeMetricsSnapshot] = []
    for theme in themes:
        member_ids = by_theme.get(theme.id, set())
        members = [corpus_by_id[i] for i in member_ids if i in corpus_by_id]
        for slice_kind, payload in slices:
            slice_corpus = [row for row in corpus if _matches(row, slice_kind, payload)]
            slice_members = [row for row in members if _matches(row, slice_kind, payload)]
            snapshots.append(
                snapshot_for_slice(
                    theme_id=theme.id,
                    cluster_run_id=run_id,
                    slice_kind=slice_kind,
                    slice_payload=payload,
                    members=slice_members,
                    corpus=slice_corpus,
                    unavailable=unavailable,
                    c_max=cfg.c_max,
                    s_max=cfg.s_max,
                )
            )
    repo.replace_theme_metrics(run_id, snapshots)
    return MetricsBatchResult(
        cluster_run_id=run_id,
        n_snapshots=len(snapshots),
        n_themes=len(themes),
    )
