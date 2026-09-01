"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { Icon } from "@/components/Icon";
import { useFilters } from "@/lib/filters";
import { useThemesQuery, useTrendsQuery } from "@/lib/hooks";
import type { TrendPoint } from "@/lib/types";

const LINE_COLORS = ["#b90041", "#4a579d", "#6d5960", "#910031", "#6370b7", "#8f6f72"];

export default function TrendsPage() {
  const { filters, setFilter } = useFilters();
  const trends = useTrendsQuery(filters);
  const themes = useThemesQuery(filters);

  if (trends.isPending && !trends.data) return <PageSkeleton />;
  if (trends.error) {
    return <ErrorState message={trends.error.message} onRetry={() => trends.refetch()} />;
  }
  const data = trends.data;
  if (!data) return null;

  const { rows, names } = pivotSeries(data.series);
  const insufficient = data.series.some((p) => p.insufficient_history);

  const rising = (themes.data?.themes ?? []).filter((t) => t.trend_direction === "rising");
  const flat = (themes.data?.themes ?? []).filter((t) => t.trend_direction === "flat");
  const declining = (themes.data?.themes ?? []).filter((t) => t.trend_direction === "declining");

  return (
    <div className="space-y-8">
      <div>
        <h2 className="mb-2 font-headline-lg text-headline-lg text-on-surface">Trends</h2>
        <p className="font-body-lg text-body-lg text-on-surface-variant">
          Mention volume over time from precomputed theme_metrics time buckets. Values are API
          mention_count, not reconstructed from quotes.
        </p>
      </div>
      {data.empty ? (
        <EmptyState
          title="No trend series"
          body="There is not enough time-bucket history for this filter slice."
        />
      ) : (
        <div className="grid grid-cols-12 gap-6">
          <section className="card-surface flex min-w-0 flex-col rounded-md lg:col-span-8">
            <div className="flex items-center justify-between p-card hairline-b">
              <h3 className="font-headline-md text-headline-md">Mention volume over time</h3>
            </div>
            <div className="h-[320px] min-w-0 p-card">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
                  <CartesianGrid stroke="#E7E1DC" />
                  <XAxis dataKey="bucket" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 11 }} allowDecimals={false} width={36} />
                  <Tooltip />
                  {names.map((name, index) => (
                    <Line
                      key={name}
                      type="monotone"
                      dataKey={name}
                      stroke={LINE_COLORS[index % LINE_COLORS.length]}
                      dot={{ r: 3 }}
                      connectNulls={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
            {insufficient ? (
              <p className="px-card pb-4 font-label-md text-label-md text-on-surface-variant">
                Some series are marked insufficient_history by the API. Single-point series are not
                drawn as a slope.
              </p>
            ) : null}
          </section>
          <section className="card-surface col-span-12 rounded-md p-card lg:col-span-4">
            <h3 className="mb-6 font-headline-md text-headline-md">Key movers</h3>
            <MoverGroup
              title="Rising"
              icon="arrow_upward"
              items={rising.map((t) => t.name)}
              onPick={(name) => {
                const theme = rising.find((t) => t.name === name);
                if (theme) setFilter("theme_id", theme.theme_id);
              }}
            />
            <hr className="my-4 border-hairline" />
            <MoverGroup
              title="Stabilizing"
              icon="horizontal_rule"
              items={flat.map((t) => t.name)}
            />
            <hr className="my-4 border-hairline" />
            <MoverGroup
              title="Declining"
              icon="arrow_downward"
              items={declining.map((t) => t.name)}
              danger
            />
          </section>
        </div>
      )}
    </div>
  );
}

function MoverGroup({
  title,
  icon,
  items,
  danger,
  onPick,
}: {
  title: string;
  icon: string;
  items: string[];
  danger?: boolean;
  onPick?: (name: string) => void;
}) {
  return (
    <div>
      <h4 className="mb-2 font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
        {title}
      </h4>
      <div className="flex flex-wrap gap-2">
        {items.length ? (
          items.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => onPick?.(name)}
              className={
                danger
                  ? "inline-flex items-center gap-1 rounded border border-dashed border-error bg-transparent px-3 py-1 font-number-data text-error"
                  : "inline-flex items-center gap-1 rounded bg-secondary-container px-3 py-1 font-number-data text-on-secondary-container"
              }
            >
              <Icon name={icon} className="text-[16px]" />
              {name}
            </button>
          ))
        ) : (
          <span className="font-body-md text-on-surface-variant">None in this slice</span>
        )}
      </div>
    </div>
  );
}

function pivotSeries(series: TrendPoint[]): {
  rows: Array<Record<string, string | number | null>>;
  names: string[];
} {
  const names: string[] = [];
  const seen = new Set<string>();
  for (const point of series) {
    if (seen.has(point.theme_name)) continue;
    seen.add(point.theme_name);
    names.push(point.theme_name);
  }
  const byBucket = new Map<string, Record<string, string | number | null>>();
  for (const point of series) {
    const row = byBucket.get(point.bucket) ?? { bucket: point.bucket };
    row[point.theme_name] = point.mention_count;
    byBucket.set(point.bucket, row);
  }
  const rows = [...byBucket.values()].sort((a, b) =>
    String(a.bucket).localeCompare(String(b.bucket)),
  );
  return { rows, names };
}
