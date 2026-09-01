from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.models.envelope import RawEnvelope


@dataclass
class IngestRun:
    id: UUID
    source_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    rows_fetched: int | None = None
    rows_upserted: int | None = None
    watermark_before: datetime | None = None
    watermark_after: datetime | None = None
    error_message: str | None = None
    source_available: bool | None = None
    payload_warning: str | None = None


@dataclass
class NormalizedRecord:
    id: UUID
    raw_id: UUID
    text_original: str
    text_en: str | None
    language: str
    product_category: str
    gender_segment: str
    price_tier: str
    platform_used: str
    occasion: str
    star_rating: int | None
    review_date: datetime | None
    quality_score: float | None
    content_hash: str
    duplicate_of: UUID | None
    eligible: bool
    pii_scrubbed_at: datetime
    normalize_run_id: UUID | None
    intent_mode: str | None = None


@dataclass
class SourceStatus:
    source_type: str
    status: str
    enabled: bool
    notes: str | None
    last_run_id: UUID | None = None
    last_run_status: str | None = None
    last_run_finished_at: datetime | None = None
    last_rows_fetched: int | None = None
    last_source_available: bool | None = None
    raw_count: int = 0
    normalized_count: int = 0


@dataclass
class ExtractionRecord:
    document_id: UUID
    content_hash: str
    prompt_version: str
    extraction_status: str
    groq_model: str | None = None
    intent_tag: str | None = None
    intent_mode: str | None = None
    friction_tags: list[str] = field(default_factory=list)
    residual_uncertainties: list[str] = field(default_factory=list)
    comparison_behavior: str | None = None
    off_platform_info_seeking: list[str] = field(default_factory=list)
    entities: dict[str, Any] = field(default_factory=dict)
    sentiment_primary: str | None = None
    sentiment_severity: float | None = None
    verbatim_quotes: list[dict[str, Any]] = field(default_factory=list)
    maps_to_questions: list[str] = field(default_factory=list)
    extraction_confidence: float | None = None
    raw_response: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    extracted_at: datetime | None = None
    metrics_eligible: bool = False

    def __post_init__(self) -> None:
        self.metrics_eligible = self.extraction_status == "ok"


@dataclass
class ChunkRecord:
    id: UUID
    document_id: UUID
    ordinal: int
    text: str
    token_count: int | None = None
    embedding: list[float] | None = None
    embedding_model: str | None = None
    embedding_revision: str | None = None
    embedding_dim: int | None = None
    content_hash: str | None = None
    source_type: str | None = None
    published_at: datetime | None = None
    product_category: str | None = None
    intent_tag: str | None = None
    intent_mode: str | None = None
    friction_tags: list[str] = field(default_factory=list)
    sentiment: str | None = None
    maps_to_questions: list[str] = field(default_factory=list)
    extraction_status: str | None = None
    similarity: float | None = None


@dataclass
class ExtractRun:
    id: UUID
    started_at: datetime
    status: str
    finished_at: datetime | None = None
    prompt_version: str | None = None
    groq_model: str | None = None
    rows_ok: int | None = None
    rows_failed: int | None = None
    rows_skipped: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    resume_after_document_id: UUID | None = None
    error_message: str | None = None


@dataclass
class EmbedRun:
    id: UUID
    started_at: datetime
    status: str
    finished_at: datetime | None = None
    embedding_model: str | None = None
    embedding_revision: str | None = None
    embedding_dim: int | None = None
    rows_encoded: int | None = None
    rows_skipped: int | None = None
    error_message: str | None = None


@dataclass
class ClusterRun:
    id: UUID
    started_at: datetime
    status: str
    mode: str
    algorithm: str
    finished_at: datetime | None = None
    params: dict[str, Any] = field(default_factory=dict)
    embedding_model: str | None = None
    embedding_revision: str | None = None
    groq_model_light: str | None = None
    prompt_version: str | None = None
    corpus: str | None = None
    n_documents: int | None = None
    n_clustered: int | None = None
    n_noise: int | None = None
    n_themes: int | None = None
    n_incremental: int | None = None
    c_max: int | None = None
    s_max: int | None = None
    error_message: str | None = None


@dataclass
class ThemeRecord:
    id: UUID
    name: str
    cluster_run_id: UUID
    description: str | None = None
    hypothesis_flag: bool = True
    bookmark_vs_stall: str = "unclear"
    published: bool = False
    label_status: str = "pending"
    centroid: list[float] | None = None
    hdbscan_label: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class DocumentTheme:
    document_id: UUID
    theme_id: UUID
    cluster_run_id: UUID
    assignment_method: str
    assignment_confidence: float | None = None


@dataclass
class NgramRow:
    id: UUID
    gram: str
    n: int
    count: int
    cluster_run_id: UUID | None = None
    theme_id: UUID | None = None
    category: str | None = None
    sentiment: str | None = None
    computed_at: datetime | None = None


@dataclass
class ChatSession:
    id: UUID
    created_at: datetime
    groq_model: str | None = None
    bge_model: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    id: UUID
    session_id: UUID
    role: str
    content: str
    created_at: datetime
    citations: list[dict[str, Any]] | None = None
    metrics_used: list[dict[str, Any]] | None = None
    tools_used: list[str] | None = None
    confidence_band: str | None = None
    status: str | None = None


@dataclass
class ReportArtifact:
    id: UUID
    title: str
    status: str
    created_at: datetime
    period_start: datetime | None = None
    period_end: datetime | None = None
    cluster_run_id: UUID | None = None
    previous_cluster_run_id: UUID | None = None
    path: str | None = None
    header: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)
    narrative: str | None = None
    groq_model: str | None = None
    error_message: str | None = None


@dataclass
class ThemeMetricsSnapshot:
    id: UUID
    theme_id: UUID
    cluster_run_id: UUID
    slice_kind: str
    slice: dict[str, Any]
    mention_count: int
    eligible_corpus_count: int
    share_of_voice: float
    source_diversity: int
    independent_source_density: int
    denominator_definition: str
    c_max: int
    s_max: int
    period_start: datetime | None = None
    period_end: datetime | None = None
    sentiment_skew: float | None = None
    sentiment_severity: float | None = None
    trend_direction: str | None = None
    segment_concentration: float | None = None
    segment_breadth: float | None = None
    data_confidence: float | None = None
    impact_score: float | None = None
    unavailable_sources: list[str] = field(default_factory=list)
    mean_extraction_confidence: float | None = None
    computed_at: datetime | None = None


class DocumentRepository(Protocol):
    def is_enabled(self, source_type: str) -> bool: ...
    def set_enabled(self, source_type: str, enabled: bool) -> None: ...
    def start_ingest_run(self, run: IngestRun) -> None: ...
    def finish_ingest_run(self, run: IngestRun) -> None: ...
    def get_watermark(self, source_type: str) -> datetime | None: ...
    def upsert_raw(self, envelope: RawEnvelope) -> tuple[UUID, bool]: ...
    def get_raw(self, raw_id: UUID) -> RawEnvelope | None: ...
    def list_raw(
        self,
        *,
        source_type: str | None = None,
        limit: int | None = None,
    ) -> list[RawEnvelope]: ...
    def list_raw_for_run(self, ingest_run_id: UUID) -> list[RawEnvelope]: ...
    def list_raw_pending_normalize(self) -> list[RawEnvelope]: ...
    def list_stale_raw(self) -> list[RawEnvelope]: ...
    def list_raw_rejected(self, limit: int = 20) -> list[RawEnvelope]: ...
    def mark_raw_decision(
        self,
        raw_id: UUID,
        relevance: str,
        reject_reason: str | None,
    ) -> None: ...
    def start_normalize_run(
        self, run_id: UUID, started_at: datetime, since_ingest_run_id: UUID | None
    ) -> None: ...
    def finish_normalize_run(
        self,
        run_id: UUID,
        finished_at: datetime,
        rows_accepted: int,
        rows_rejected: int,
        status: str,
    ) -> None: ...
    def find_normalized_by_content_hash(self, content_hash: str) -> UUID | None: ...
    def get_normalized_by_raw_id(self, raw_id: UUID) -> NormalizedRecord | None: ...
    def upsert_normalized(self, record: NormalizedRecord) -> None: ...
    def count_raw(self, source_type: str | None = None) -> int: ...
    def count_normalized(self, source_type: str | None = None) -> int: ...
    def list_source_status(self) -> list[SourceStatus]: ...
    def list_ingest_queries(self) -> list[dict[str, Any]]: ...
    def list_normalized(
        self,
        limit: int | None = 20,
        *,
        eligible_only: bool = False,
        random_sample: bool = False,
        source_type: str | None = None,
        copy: bool = True,
    ) -> list[NormalizedRecord]: ...
    def get_normalized(self, document_id: UUID) -> NormalizedRecord | None: ...
    def set_normalized_intent_mode(self, document_id: UUID, intent_mode: str | None) -> None: ...
    def list_extract_candidates(
        self,
        *,
        resume_after: UUID | None = None,
        limit: int | None = None,
        retry_failed: bool = True,
    ) -> list[NormalizedRecord]: ...
    def get_extraction(self, document_id: UUID) -> ExtractionRecord | None: ...
    def upsert_extraction(self, record: ExtractionRecord) -> None: ...
    def list_extractions(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        metrics_eligible_only: bool = False,
        copy: bool = True,
    ) -> list[ExtractionRecord]: ...
    def start_extract_run(self, run: ExtractRun) -> None: ...
    def finish_extract_run(self, run: ExtractRun) -> None: ...
    def list_embed_candidates(self, *, limit: int | None = None) -> list[NormalizedRecord]: ...
    def list_chunks(self, document_id: UUID | None = None, *, copy: bool = True) -> list[ChunkRecord]: ...
    def replace_chunks(self, document_id: UUID, chunks: list[ChunkRecord]) -> None: ...
    def update_chunk_embedding(
        self,
        chunk_id: UUID,
        embedding: list[float],
        *,
        embedding_model: str,
        embedding_revision: str | None,
        embedding_dim: int,
    ) -> None: ...
    def update_chunk_metadata(self, document_id: UUID, extraction: ExtractionRecord) -> None: ...
    def distinct_embedding_models(self) -> list[str]: ...
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
    ) -> list[ChunkRecord]: ...
    def list_ingest_runs(self, source_type: str | None = None) -> list[IngestRun]: ...
    def replace_ngrams(self, cluster_run_id: UUID, rows: list[NgramRow]) -> None: ...
    def list_ngrams(
        self,
        *,
        cluster_run_id: UUID | None = None,
        theme_id: UUID | None = None,
        category: str | None = None,
        sentiment: str | None = None,
        n: int | None = None,
        limit: int | None = 50,
    ) -> list[NgramRow]: ...
    def insert_chat_session(self, session: ChatSession) -> None: ...
    def get_chat_session(self, session_id: UUID) -> ChatSession | None: ...
    def insert_chat_message(self, message: ChatMessage) -> None: ...
    def list_chat_messages(self, session_id: UUID) -> list[ChatMessage]: ...
    def insert_report(self, artifact: ReportArtifact) -> None: ...
    def list_reports(self) -> list[ReportArtifact]: ...
    def get_report(self, report_id: UUID) -> ReportArtifact | None: ...
    def start_embed_run(self, run: EmbedRun) -> None: ...
    def finish_embed_run(self, run: EmbedRun) -> None: ...
    def start_cluster_run(self, run: ClusterRun) -> None: ...
    def finish_cluster_run(self, run: ClusterRun) -> None: ...
    def list_cluster_runs(self) -> list[ClusterRun]: ...
    def get_cluster_run(self, run_id: UUID) -> ClusterRun | None: ...
    def latest_cluster_run(self, *, success_only: bool = True) -> ClusterRun | None: ...
    def upsert_theme(self, theme: ThemeRecord) -> None: ...
    def get_theme(self, theme_id: UUID) -> ThemeRecord | None: ...
    def list_themes(self, cluster_run_id: UUID | None = None) -> list[ThemeRecord]: ...
    def replace_document_themes(
        self, cluster_run_id: UUID, rows: list[DocumentTheme]
    ) -> None: ...
    def list_document_themes(
        self,
        *,
        cluster_run_id: UUID | None = None,
        theme_id: UUID | None = None,
    ) -> list[DocumentTheme]: ...
    def replace_theme_metrics(
        self, cluster_run_id: UUID, rows: list[ThemeMetricsSnapshot]
    ) -> None: ...
    def list_theme_metrics(
        self,
        *,
        cluster_run_id: UUID | None = None,
        slice_kind: str | None = None,
        published_only: bool = False,
    ) -> list[ThemeMetricsSnapshot]: ...
