import { API_KEY_STORAGE } from "./constants";
import { apiQueryFromFilters, type FilterState } from "./filters";
import {
  ApiError,
  type CopilotTurnResponse,
  type EvidenceResponse,
  type NgramsResponse,
  type OverviewResponse,
  type ReportDetail,
  type ReportsResponse,
  type SegmentsResponse,
  type ThemesResponse,
  type TrendsResponse,
} from "./types";

const QUERY_PREFIX = "/api/query";

function storedKey(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(API_KEY_STORAGE) ?? "";
}

export function setStoredApiKey(value: string): void {
  if (typeof window === "undefined") return;
  if (value) window.sessionStorage.setItem(API_KEY_STORAGE, value);
  else window.sessionStorage.removeItem(API_KEY_STORAGE);
}

function errorMessageFromBody(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const body = payload as Record<string, unknown>;
  if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  if (Array.isArray(body.detail)) {
    const parts = body.detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return "";
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  if (typeof body.message === "string" && body.message.trim()) {
    if (/application failed to respond/i.test(body.message)) {
      return (
        "Query API is not reachable (502). Set API_BASE_URL to https://<api>.up.railway.app " +
        "(the web service, not Postgres), and confirm /health returns store=postgres."
      );
    }
    return body.message;
  }
  return "";
}

async function parseError(res: Response): Promise<string> {
  try {
    const text = errorMessageFromBody(await res.json());
    if (text) return text;
  } catch {
    /* ignore */
  }
  const statusText = (res.statusText || "").trim();
  if (res.status === 404) {
    return (
      "Query API returned 404. Confirm Vercel Root Directory is web, API_BASE_URL is https://<api>.up.railway.app (no path), then Redeploy."
    );
  }
  if (statusText) return res.status ? `${res.status} ${statusText}` : statusText;
  return res.status ? `Request failed (${res.status})` : "Request failed";
}

export async function queryFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const key = storedKey();
  if (key) headers.set("X-API-Key", key);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${QUERY_PREFIX}${path}`, { ...init, headers });
  if (!res.ok) {
    throw new ApiError(await parseError(res), res.status);
  }
  return res;
}

function withQuery(path: string, filters: FilterState, extra?: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams(apiQueryFromFilters(filters));
  if (extra) {
    for (const [key, value] of Object.entries(extra)) {
      if (value == null || value === "") continue;
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function fetchOverview(filters: FilterState): Promise<OverviewResponse> {
  const res = await queryFetch(withQuery("/metrics/overview", filters));
  return (await res.json()) as OverviewResponse;
}

export async function fetchThemes(filters: FilterState): Promise<ThemesResponse> {
  const res = await queryFetch(withQuery("/metrics/themes", filters));
  return (await res.json()) as ThemesResponse;
}

export async function fetchSegments(
  filters: FilterState,
  dimension: string,
): Promise<SegmentsResponse> {
  const res = await queryFetch(withQuery("/metrics/segments", filters, { dimension }));
  return (await res.json()) as SegmentsResponse;
}

export async function fetchTrends(filters: FilterState): Promise<TrendsResponse> {
  const res = await queryFetch(withQuery("/metrics/trends", filters));
  return (await res.json()) as TrendsResponse;
}

export async function fetchNgrams(
  filters: FilterState,
  extra?: { n?: number; limit?: number },
): Promise<NgramsResponse> {
  const res = await queryFetch(
    withQuery("/metrics/ngrams", filters, {
      n: extra?.n,
      limit: extra?.limit ?? 80,
    }),
  );
  return (await res.json()) as NgramsResponse;
}

export async function fetchEvidence(
  filters: FilterState,
  extra?: { limit?: number },
): Promise<EvidenceResponse> {
  const res = await queryFetch(withQuery("/evidence", filters, { limit: extra?.limit ?? 200 }));
  return (await res.json()) as EvidenceResponse;
}

export function evidenceCsvUrl(filters: FilterState): string {
  return `${QUERY_PREFIX}${withQuery("/evidence", filters, { format: "csv" })}`;
}

export async function downloadEvidenceCsv(filters: FilterState): Promise<void> {
  const res = await queryFetch(withQuery("/evidence", filters, { format: "csv" }));
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "evidence-scrubbed.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function fetchReports(): Promise<ReportsResponse> {
  const res = await queryFetch("/reports");
  return (await res.json()) as ReportsResponse;
}

export async function fetchReportJson(id: string): Promise<ReportDetail> {
  const res = await queryFetch(`/reports/${id}?format=json`);
  return (await res.json()) as ReportDetail;
}

export async function downloadReportPdf(id: string): Promise<void> {
  const res = await queryFetch(`/reports/${id}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${id}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function queryCopilot(
  question: string,
  filters: FilterState,
  sessionId?: string | null,
): Promise<CopilotTurnResponse> {
  const body = {
    question,
    session_id: sessionId || undefined,
    date_from: filters.date_from,
    date_to: filters.date_to,
    source_type: filters.source_type,
    product_category: filters.product_category,
    gender_segment: filters.gender_segment,
    price_tier: filters.price_tier,
    platform_used: filters.platform_used,
    intent_mode: filters.intent_mode,
    theme_id: filters.theme_id,
  };
  const res = await queryFetch("/copilot/query", {
    method: "POST",
    body: JSON.stringify(body),
    ...(typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function"
      ? { signal: AbortSignal.timeout(60_000) }
      : {}),
  });
  return (await res.json()) as CopilotTurnResponse;
}

export async function fetchHealth(): Promise<{ status: string; store?: string; detail?: string }> {
  const headers = new Headers();
  const key = storedKey();
  if (key) headers.set("X-API-Key", key);
  const res = await fetch(`${QUERY_PREFIX}/health`, { headers, cache: "no-store" });
  const body = (await res.json().catch(() => ({}))) as {
    status?: string;
    store?: string;
    detail?: string;
  };
  if (res.status === 503 && body.store === "pending") {
    throw new ApiError(
      body.detail || "Query API is connecting to Postgres. Retry in a few seconds.",
      503,
    );
  }
  if (!res.ok) {
    throw new ApiError(errorMessageFromBody(body) || `Request failed (${res.status})`, res.status);
  }
  return { status: body.status || "ok", store: body.store, detail: body.detail };
}
