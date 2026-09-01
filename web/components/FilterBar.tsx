"use client";

import { useIsFetching } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Icon } from "@/components/Icon";
import { SOURCE_LABELS, SOURCE_TYPES } from "@/lib/constants";
import { ninetyDaysAgoIso, todayIso, useFilters } from "@/lib/filters";
import { formatDateTime, sourceLabel } from "@/lib/format";
import { useThemesQuery } from "@/lib/hooks";
import { ingestedSourceRows } from "@/lib/sources";
import type { OverviewResponse, ThemesResponse } from "@/lib/types";

const CATEGORY_HINTS = [
  "unknown",
  "ethnic",
  "western",
  "footwear",
  "accessories",
  "dresses",
  "activewear",
];

const INTENT_LABELS: Record<string, string> = {
  passive_bookmark: "bookmark (save for later)",
  bookmark: "bookmark (save for later)",
  near_term_purchase: "stall (near-term purchase)",
  stall: "stall (near-term purchase)",
  mixed: "mixed",
  unknown: "unknown",
  unclear: "unknown",
};

export function FilterBar({
  overview,
  themes,
  onMenuClick,
}: {
  overview?: OverviewResponse;
  themes?: ThemesResponse;
  onMenuClick: () => void;
}) {
  const { filters, setFilter, setFilters } = useFilters();
  const catalog = useThemesQuery({});
  const fetching = useIsFetching();
  const [copied, setCopied] = useState(false);
  const ingested = ingestedSourceRows(overview?.counts_by_source);
  const sources = ingested.length
    ? ingested.map((s) => s.source_type)
    : [...SOURCE_TYPES].filter((name) => name === "play_store" || name === "app_store");
  const themeOptions = catalog.data?.themes ?? themes?.themes ?? [];
  const categories = unique([
    filters.product_category,
    ...CATEGORY_HINTS,
    ...themeOptions.flatMap((t) =>
      t.slice && typeof t.slice.product_category === "string" ? [t.slice.product_category] : [],
    ),
  ]);
  const intentOptions = useMemo(() => {
    const keys = new Set([
      "passive_bookmark",
      "near_term_purchase",
      "unknown",
      ...Object.keys(overview?.intent_mode_counts ?? {}),
    ]);
    if (filters.intent_mode) keys.add(filters.intent_mode);
    return [...keys].map((key) => [key, INTENT_LABELS[key] ?? key] as [string, string]);
  }, [overview?.intent_mode_counts, filters.intent_mode]);

  const datePreset = datePresetValue(filters.date_from, filters.date_to);

  async function share() {
    await navigator.clipboard.writeText(window.location.href);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <header className="z-20 min-w-0 border-b border-outline-variant bg-surface px-3 py-3 md:px-gutter">
      <div className="flex min-w-0 items-start gap-3">
        <button
          type="button"
          className="mt-0.5 shrink-0 rounded-md p-1.5 text-on-surface hover:bg-surface-container-high md:hidden"
          onClick={onMenuClick}
          aria-label="Open navigation"
        >
          <Icon name="menu" />
        </button>
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="date-preset">
            Date range
          </label>
          <select
            id="date-preset"
            className="focus-ring max-w-full rounded-md border border-hairline bg-surface px-2 py-1.5 font-body-md text-body-md text-on-surface"
            value={datePreset}
            onChange={(e) => {
              if (e.target.value === "all") {
                setFilters({ date_from: null, date_to: null });
              } else if (e.target.value === "90") {
                setFilters({ date_from: ninetyDaysAgoIso(), date_to: todayIso() });
              }
            }}
          >
            <option value="all">All dates</option>
            <option value="90">Last 90 days</option>
            {datePreset === "custom" ? <option value="custom">Custom dates</option> : null}
          </select>
          <input
            type="date"
            aria-label="From date"
            value={(filters.date_from ?? "").slice(0, 10)}
            onChange={(e) => setFilter("date_from", e.target.value || null)}
            className="focus-ring w-[10rem] max-w-full min-w-0 rounded-md border border-hairline bg-surface px-2 py-1.5 font-body-md text-body-md text-on-surface"
          />
          <input
            type="date"
            aria-label="To date"
            value={(filters.date_to ?? "").slice(0, 10)}
            onChange={(e) => setFilter("date_to", e.target.value || null)}
            className="focus-ring w-[10rem] max-w-full min-w-0 rounded-md border border-hairline bg-surface px-2 py-1.5 font-body-md text-body-md text-on-surface"
          />
          <Select
            label="Source"
            value={filters.source_type ?? ""}
            onChange={(value) => setFilter("source_type", value || null)}
            options={sources.map((s) => [s, sourceLabel(s, SOURCE_LABELS)])}
            emptyLabel="All sources"
          />
          <Select
            label="Category"
            value={filters.product_category ?? ""}
            onChange={(value) => setFilter("product_category", value || null)}
            options={categories.map((c) => [c, c])}
            emptyLabel="All categories"
          />
          <Select
            label="Intent"
            value={filters.intent_mode ?? ""}
            onChange={(value) => setFilter("intent_mode", value || null)}
            options={intentOptions}
            emptyLabel="All intent modes"
          />
          <Select
            label="Gender"
            value={filters.gender_segment ?? ""}
            onChange={(value) => setFilter("gender_segment", value || null)}
            options={[
              ["unknown", "unknown"],
              ["women", "women"],
              ["men", "men"],
            ]}
            emptyLabel="All gender"
          />
          <Select
            label="Price"
            value={filters.price_tier ?? ""}
            onChange={(value) => setFilter("price_tier", value || null)}
            options={[
              ["unknown", "unknown"],
              ["budget", "budget"],
              ["mid", "mid"],
              ["premium", "premium"],
            ]}
            emptyLabel="All price"
          />
          <Select
            label="Theme"
            value={filters.theme_id ?? ""}
            onChange={(value) => setFilter("theme_id", value || null)}
            options={themeOptions.map((t) => [t.theme_id, t.name])}
            emptyLabel="All themes"
          />
          {fetching > 0 ? (
            <span className="font-label-md text-label-md text-on-surface-variant">Updating…</span>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <p className="hidden max-w-[16rem] truncate font-label-md text-label-md text-on-surface-variant 2xl:block">
            {overview?.last_ingest?.finished_at
              ? `ingest ${formatDateTime(overview.last_ingest.finished_at)}`
              : "no ingest yet"}
            {themes?.themes_refreshed_at
              ? ` · themes refreshed ${formatDateTime(themes.themes_refreshed_at)}`
              : ""}
          </p>
          <button
            type="button"
            onClick={share}
            className="hidden rounded-md border border-hairline px-3 py-1.5 font-label-md text-label-md text-on-surface hover:bg-surface-container-low lg:block"
          >
            {copied ? "Link copied" : "Share view"}
          </button>
          <img
            src="/images/avatar.jpg"
            alt=""
            width={32}
            height={32}
            className="h-8 w-8 rounded-full border border-outline-variant object-cover"
          />
        </div>
      </div>
    </header>
  );
}

function datePresetValue(dateFrom?: string, dateTo?: string): string {
  if (!dateFrom && !dateTo) return "all";
  if (dateFrom === ninetyDaysAgoIso() && dateTo === todayIso()) return "90";
  return "custom";
}

function Select({
  label,
  value,
  onChange,
  options,
  emptyLabel,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<[string, string]>;
  emptyLabel: string;
}) {
  const seen = new Set<string>();
  const uniqueOptions = options.filter(([key]) => {
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return (
    <label className="flex max-w-full items-center font-body-md text-body-md text-on-surface-variant">
      <span className="sr-only">{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="focus-ring max-w-[11rem] rounded-md border border-hairline bg-surface px-2 py-1.5 text-on-surface"
      >
        <option value="">{emptyLabel}</option>
        {uniqueOptions.map(([key, text]) => (
          <option key={key} value={key}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

function unique(values: Array<string | undefined | null>): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}
