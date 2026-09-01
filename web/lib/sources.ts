import { SOURCE_LABELS } from "@/lib/constants";
import type { SourceVolume } from "@/lib/types";

/** Store feeds that belong in Overview / Copilot outage chrome. */
const OPERATOR_SOURCES = new Set(["play_store", "app_store"]);

/**
 * Rows the dashboard should render. Catalog platforms without a live pull
 * (Instagram, Facebook, Quora, on-site Myntra, unconfigured YouTube/X, …)
 * stay out of Overview, Copilot, and Sources — they are not a backend outage.
 */
export function ingestedSourceRows(rows: SourceVolume[] | undefined): SourceVolume[] {
  if (!rows?.length) return [];
  return rows.filter(
    (row) => row.status === "live" || row.raw_count > 0 || row.eligible_count > 0,
  );
}

/** Play/App Store failures only. Never list catalog / unconfigured APIs. */
export function operatorUnavailable(sources: string[] | undefined): string[] {
  if (!sources?.length) return [];
  return sources.filter((name) => OPERATOR_SOURCES.has(name));
}

export function labeledSources(names: string[]): string {
  return names.map((name) => SOURCE_LABELS[name] ?? name).join(", ");
}
