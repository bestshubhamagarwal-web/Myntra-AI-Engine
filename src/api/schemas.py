"""Frozen HTTP response models for Phase 6. Numbers come from theme_metrics."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FilterEcho(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    source_type: str | None = None
    product_category: str | None = None
    gender_segment: str | None = None
    price_tier: str | None = None
    platform_used: str | None = None
    intent_mode: str | None = None
    theme_id: str | None = None
    friction_tag: str | None = None
    intent_tag: str | None = None
    q: str | None = None


class SourceVolume(BaseModel):
    source_type: str
    status: str
    enabled: bool
    raw_count: int = 0
    normalized_count: int = 0
    eligible_count: int = 0
    volume_is_current: bool = False
    last_run_status: str | None = None
    last_successful_pull: str | None = None
    notes: str | None = None


class DateBucket(BaseModel):
    bucket: str
    count: int


class OverviewResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "cluster_run_id": "00000000-0000-0000-0000-000000000001",
                    "themes_refreshed_at": "2026-09-01T00:00:00+00:00",
                    "denominator_definition": "eligible_normalized_after_relevance_and_quality",
                    "eligible_corpus_count": 40,
                    "unavailable_sources": ["instagram", "facebook"],
                    "included_sources": ["play_store", "reddit"],
                    "counts_by_source": [],
                    "date_histogram": [],
                    "intent_tag_counts": {},
                    "intent_mode_counts": {},
                    "filters": {},
                }
            ]
        }
    )
    cluster_run_id: str | None = None
    themes_refreshed_at: str | None = None
    corpus: str | None = None
    denominator_definition: str
    eligible_corpus_count: int
    normalized_count: int
    raw_count: int
    counts_by_source: list[SourceVolume]
    unavailable_sources: list[str]
    included_sources: list[str]
    date_histogram: list[DateBucket]
    intent_tag_counts: dict[str, int] = Field(default_factory=dict)
    intent_mode_counts: dict[str, int] = Field(default_factory=dict)
    last_ingest: dict[str, Any] | None = None
    filters: FilterEcho
    empty: bool = False


class SparkPoint(BaseModel):
    bucket: str
    mention_count: int
    share_of_voice: float | None = None


class ThemeCard(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "theme_id": "00000000-0000-0000-0000-000000000002",
                    "name": "Fit uncertainty on kurtas",
                    "rank": 1,
                    "mention_count": 12,
                    "share_of_voice": 0.3,
                    "data_confidence": 0.55,
                    "impact_score": 0.04,
                    "unavailable_sources": ["instagram"],
                    "bookmark_vs_stall": "stall",
                    "hypothesis_flag": True,
                }
            ]
        }
    )
    theme_id: str
    name: str
    description: str | None = None
    rank: int
    mention_count: int
    share_of_voice: float
    data_confidence: float | None = None
    confidence_band: str
    sentiment_severity: float | None = None
    sentiment_skew: float | None = None
    impact_score: float | None = None
    source_diversity: int | None = None
    independent_source_density: int | None = None
    trend_direction: str | None = None
    segment_concentration: float | None = None
    segment_breadth: float | None = None
    unavailable_sources: list[str]
    eligible_corpus_count: int
    denominator_definition: str
    hypothesis_flag: bool
    bookmark_vs_stall: str
    slice_kind: str
    slice: dict[str, Any]
    sparkline: list[SparkPoint] = Field(default_factory=list)
    sparkline_insufficient: bool = True
    evidence_count: int = 0
    filtered_evidence_count: int = 0
    cluster_run_id: str
    themes_refreshed_at: str | None = None


class ThemesResponse(BaseModel):
    cluster_run_id: str | None = None
    themes_refreshed_at: str | None = None
    denominator_definition: str
    unavailable_sources: list[str]
    metrics_slice: dict[str, Any]
    filters: FilterEcho
    themes: list[ThemeCard]
    empty: bool = False


class SegmentCell(BaseModel):
    theme_id: str
    theme_name: str
    dimension: str
    segment: str
    mention_count: int
    eligible_corpus_count: int
    share_of_voice: float
    data_confidence: float | None = None
    impact_score: float | None = None
    unavailable_sources: list[str]
    small_n: bool
    caveat: str | None = None
    from_snapshot: bool = True


class SegmentsResponse(BaseModel):
    dimension: str
    unknown_visible: bool = True
    small_n_threshold: int
    filters: FilterEcho
    unavailable_sources: list[str]
    cells: list[SegmentCell]
    empty: bool = False


class TrendPoint(BaseModel):
    theme_id: str
    theme_name: str
    bucket: str
    mention_count: int
    share_of_voice: float
    insufficient_history: bool = False


class TrendsResponse(BaseModel):
    filters: FilterEcho
    unavailable_sources: list[str]
    series: list[TrendPoint]
    empty: bool = False


class NgramRowOut(BaseModel):
    gram: str
    n: int
    count: int
    theme_id: str | None = None
    category: str | None = None
    sentiment: str | None = None


class NgramsResponse(BaseModel):
    filters: FilterEcho
    cloud_eligible: bool
    rows: list[NgramRowOut]
    empty: bool = False


class EvidenceRow(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "document_id": "00000000-0000-0000-0000-000000000003",
                    "chunk_id": "00000000-0000-0000-0000-000000000004",
                    "theme_id": "00000000-0000-0000-0000-000000000002",
                    "quote": "Kurta runs small so it sits in my wishlist",
                    "source_type": "play_store",
                    "url": "https://play.google.com/store/apps/details?id=com.myntra.android",
                    "link_unavailable": False,
                    "published_at": "2026-08-20",
                }
            ]
        }
    )
    document_id: str
    chunk_id: str | None = None
    theme_id: str | None = None
    theme_name: str | None = None
    quote: str
    source_type: str
    url: str | None = None
    link_unavailable: bool
    published_at: str | None = None
    product_category: str | None = None
    intent_mode: str | None = None
    intent_tag: str | None = None
    friction_tags: list[str] = Field(default_factory=list)
    sentiment: str | None = None
    maps_to_questions: list[str] = Field(default_factory=list)


class EvidenceResponse(BaseModel):
    filters: FilterEcho
    rows: list[EvidenceRow]
    empty: bool = False


class ReportListItem(BaseModel):
    id: str
    title: str
    status: str
    created_at: str
    path: str | None = None
    cluster_run_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    header: dict[str, Any] = Field(default_factory=dict)
    narrative: str | None = None
    top_themes: list[dict[str, Any]] = Field(default_factory=list)


class ReportsResponse(BaseModel):
    reports: list[ReportListItem]
    empty: bool = False


class Citation(BaseModel):
    document_id: str
    chunk_id: str | None = None
    url: str | None = None
    source_type: str
    quote: str
    published_at: str | None = None


class CopilotQueryRequest(BaseModel):
    question: str
    session_id: UUID | None = None
    date_from: str | None = None
    date_to: str | None = None
    source_type: str | None = None
    product_category: str | None = None
    gender_segment: str | None = None
    price_tier: str | None = None
    platform_used: str | None = None
    intent_mode: str | None = None
    theme_id: str | None = None


class CopilotTurnResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "00000000-0000-0000-0000-000000000010",
                    "status": "ok",
                    "answer": "Fit uncertainty is 30% of eligible mentions (n=12).",
                    "confidence_band": "caveat",
                    "unavailable_sources": ["instagram"],
                    "citations": [],
                    "tools_used": ["get_metrics_themes", "get_evidence"],
                }
            ]
        }
    )
    session_id: str
    status: str
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    metrics_used: list[dict[str, Any]] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    confidence_band: str
    data_confidence: float | None = None
    unavailable_sources: list[str] = Field(default_factory=list)
    hypothesis_flags: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    intent: str | None = None
    tool_injection_ignored: bool = False
    error: str | None = None
    filters: FilterEcho | None = None


PHASE6_PATHS: tuple[str, ...] = (
    "/metrics/overview",
    "/metrics/themes",
    "/metrics/segments",
    "/metrics/trends",
    "/metrics/ngrams",
    "/evidence",
    "/reports",
    "/reports/{report_id}",
    "/copilot/query",
)
