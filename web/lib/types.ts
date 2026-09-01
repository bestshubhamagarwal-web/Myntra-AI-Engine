export type FilterEcho = {
  date_from?: string | null;
  date_to?: string | null;
  source_type?: string | null;
  product_category?: string | null;
  gender_segment?: string | null;
  price_tier?: string | null;
  platform_used?: string | null;
  intent_mode?: string | null;
  theme_id?: string | null;
  friction_tag?: string | null;
  intent_tag?: string | null;
  q?: string | null;
};

export type SourceVolume = {
  source_type: string;
  status: string;
  enabled: boolean;
  raw_count: number;
  normalized_count: number;
  eligible_count: number;
  volume_is_current: boolean;
  last_run_status: string | null;
  last_successful_pull: string | null;
  notes: string | null;
};

export type DateBucket = {
  bucket: string;
  count: number;
};

export type OverviewResponse = {
  cluster_run_id: string | null;
  themes_refreshed_at: string | null;
  corpus: string | null;
  denominator_definition: string;
  eligible_corpus_count: number;
  normalized_count: number;
  raw_count: number;
  counts_by_source: SourceVolume[];
  unavailable_sources: string[];
  included_sources: string[];
  date_histogram: DateBucket[];
  intent_tag_counts: Record<string, number>;
  intent_mode_counts: Record<string, number>;
  last_ingest: {
    id: string;
    source_type: string;
    status: string;
    finished_at: string | null;
    source_available: boolean;
  } | null;
  filters: FilterEcho;
  empty: boolean;
};

export type SparkPoint = {
  bucket: string;
  mention_count: number;
  share_of_voice: number | null;
};

export type ThemeCard = {
  theme_id: string;
  name: string;
  description: string | null;
  rank: number;
  mention_count: number;
  share_of_voice: number;
  data_confidence: number | null;
  confidence_band: string;
  sentiment_severity: number | null;
  sentiment_skew: number | null;
  impact_score: number | null;
  source_diversity: number | null;
  independent_source_density: number | null;
  trend_direction: string | null;
  segment_concentration: number | null;
  segment_breadth: number | null;
  unavailable_sources: string[];
  eligible_corpus_count: number;
  denominator_definition: string;
  hypothesis_flag: boolean;
  bookmark_vs_stall: string;
  slice_kind: string;
  slice: Record<string, unknown>;
  sparkline: SparkPoint[];
  sparkline_insufficient: boolean;
  evidence_count: number;
  filtered_evidence_count: number;
  cluster_run_id: string;
  themes_refreshed_at: string | null;
};

export type ThemesResponse = {
  cluster_run_id: string | null;
  themes_refreshed_at: string | null;
  denominator_definition: string;
  unavailable_sources: string[];
  metrics_slice: Record<string, unknown>;
  filters: FilterEcho;
  themes: ThemeCard[];
  empty: boolean;
};

export type SegmentCell = {
  theme_id: string;
  theme_name: string;
  dimension: string;
  segment: string;
  mention_count: number;
  eligible_corpus_count: number;
  share_of_voice: number;
  data_confidence: number | null;
  impact_score: number | null;
  unavailable_sources: string[];
  small_n: boolean;
  caveat: string | null;
  from_snapshot: boolean;
};

export type SegmentsResponse = {
  dimension: string;
  unknown_visible: boolean;
  small_n_threshold: number;
  filters: FilterEcho;
  unavailable_sources: string[];
  cells: SegmentCell[];
  empty: boolean;
};

export type TrendPoint = {
  theme_id: string;
  theme_name: string;
  bucket: string;
  mention_count: number;
  share_of_voice: number;
  insufficient_history: boolean;
};

export type TrendsResponse = {
  filters: FilterEcho;
  unavailable_sources: string[];
  series: TrendPoint[];
  empty: boolean;
};

export type NgramRow = {
  gram: string;
  n: number;
  count: number;
  theme_id: string | null;
  category: string | null;
  sentiment: string | null;
};

export type NgramsResponse = {
  filters: FilterEcho;
  cloud_eligible: boolean;
  rows: NgramRow[];
  empty: boolean;
};

export type EvidenceRow = {
  document_id: string;
  chunk_id: string | null;
  theme_id: string | null;
  theme_name: string | null;
  quote: string;
  source_type: string;
  url: string | null;
  link_unavailable: boolean;
  published_at: string | null;
  product_category: string | null;
  intent_mode: string | null;
  intent_tag: string | null;
  friction_tags: string[];
  sentiment: string | null;
  maps_to_questions: string[];
};

export type EvidenceResponse = {
  filters: FilterEcho;
  rows: EvidenceRow[];
  empty: boolean;
};

export type ReportListItem = {
  id: string;
  title: string;
  status: string;
  created_at: string;
  path: string | null;
  cluster_run_id: string | null;
  period_start: string | null;
  period_end: string | null;
  header: Record<string, unknown>;
  narrative?: string | null;
  top_themes?: Array<{
    theme_id?: string;
    name?: string;
    mention_count?: number;
    share_of_voice?: number;
    impact_score?: number;
  }>;
};

export type ReportsResponse = {
  reports: ReportListItem[];
  empty: boolean;
};

export type ReportDetail = {
  id: string;
  title: string;
  status: string;
  header: Record<string, unknown>;
  diff: unknown;
  narrative: string | null;
  path: string | null;
  top_themes?: ReportListItem["top_themes"];
};

export type Citation = {
  document_id: string;
  chunk_id: string | null;
  url: string | null;
  source_type: string;
  quote: string;
  published_at: string | null;
};

export type CopilotTurnResponse = {
  session_id: string;
  status: string;
  answer: string | null;
  citations: Citation[];
  metrics_used: Array<Record<string, unknown>>;
  tools_used: string[];
  confidence_band: string;
  data_confidence: number | null;
  unavailable_sources: string[];
  hypothesis_flags: string[];
  latency_ms: number;
  error: string | null;
  filters: FilterEcho | null;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
