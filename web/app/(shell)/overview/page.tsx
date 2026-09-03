"use client";

import Link from "next/link";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { UnavailableBanner } from "@/components/UnavailableBanner";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { Icon } from "@/components/Icon";
import { SOURCE_LABELS } from "@/lib/constants";
import { useFilters, withCurrentFilters } from "@/lib/filters";
import { formatInteger, formatShareOfVoice, rankLabel, sourceLabel } from "@/lib/format";
import { useOverviewQuery, useThemesQuery } from "@/lib/hooks";
import { ingestedSourceRows } from "@/lib/sources";

export default function OverviewPage() {
  const { filters, searchParams, setFilters } = useFilters();
  const overview = useOverviewQuery(filters);
  const themes = useThemesQuery(filters);

  if ((overview.isPending && !overview.data) || (themes.isPending && !themes.data)) {
    return <PageSkeleton />;
  }
  if (overview.error) {
    return <ErrorState message={overview.error.message} onRetry={() => overview.refetch()} />;
  }
  const data = overview.data;
  if (!data) return null;

  const sourceRows = ingestedSourceRows(data.counts_by_source);
  const liveCount = sourceRows.filter((row) => row.status === "live").length;
  const sourceCount = sourceRows.length;
  const topThemes = themes.data?.themes.slice(0, 5) ?? [];
  const intentEntries = Object.entries(data.intent_mode_counts);
  const tagEntries = Object.entries(data.intent_tag_counts);
  let tagPeak = 0;
  for (const [, count] of tagEntries) {
    if (count > tagPeak) tagPeak = count;
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-8">
      <UnavailableBanner overview={data} />
      {data.empty ? (
        <EmptyState
          title="No documents in this slice"
          body={
            data.raw_count === 0 && data.normalized_count === 0
              ? "Postgres is connected but the hosted corpus is empty. From your laptop, set DATABASE_URL to the same Neon URL as the Vercel API project, then run python -m src.cli sync-postgres (or pipeline) to load public reviews."
              : "The current filters match zero eligible documents. Clear a filter or wait for ingest. Nothing is filled in from a previous period."
          }
        />
      ) : null}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
        <Kpi label="Eligible corpus" value={formatInteger(data.eligible_corpus_count)} />
        <Kpi
          label="Live sources"
          value={`${liveCount}`}
          suffix={`/ ${sourceCount}`}
        />
        <Kpi
          label="Opportunity areas"
          value={formatInteger(themes.data?.themes.length ?? 0)}
        />
        <Kpi label="Normalized docs" value={formatInteger(data.normalized_count)} />
      </div>
      <p className="font-label-md text-label-md text-on-surface-variant">
        SoV denominator: {data.denominator_definition}
      </p>

      <div className="grid min-w-0 grid-cols-12 gap-6">
        <section className="card-surface col-span-12 flex min-h-[360px] min-w-0 flex-col rounded-lg lg:col-span-8">
          <div className="flex flex-wrap items-center justify-between gap-2 p-4 hairline-b">
            <h2 className="font-headline-md text-headline-md text-on-surface">
              Eligible documents by week
            </h2>
            <span className="rounded bg-level-0 px-2 py-1 font-label-md text-label-md text-on-surface-variant">
              API date histogram
            </span>
          </div>
          <div className="h-[280px] min-w-0 p-4">
            {data.date_histogram.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.date_histogram} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
                  <CartesianGrid stroke="#E7E1DC" vertical={false} />
                  <XAxis dataKey="bucket" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 11 }} allowDecimals={false} width={36} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#b90041" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="p-6 font-body-md text-on-surface-variant">No dated documents in this slice.</p>
            )}
          </div>
        </section>

          <section className="card-surface col-span-12 flex min-h-[360px] min-w-0 flex-col rounded-lg lg:col-span-4">
          <div className="p-4 hairline-b">
            <h2 className="font-headline-md text-headline-md text-on-surface">Source mix</h2>
          </div>
          <div className="flex flex-1 flex-col justify-center gap-3 p-6">
            {sourceRows.map((row) => (
              <div key={row.source_type} className="flex min-w-0 items-center gap-3">
                <div className="w-20 shrink-0 truncate text-right font-label-md text-[12px] font-medium text-on-surface sm:w-24">
                  {sourceLabel(row.source_type, SOURCE_LABELS)}
                </div>
                <div className="h-4 min-w-0 flex-1 overflow-hidden rounded-sm bg-level-0">
                  <div
                    className="h-full bg-primary"
                    style={{
                      width: barWidth(row.eligible_count, data.eligible_corpus_count),
                    }}
                  />
                </div>
                <div className="w-14 shrink-0 font-number-data text-[12px] text-on-surface-variant tnum sm:w-16">
                  {formatInteger(row.eligible_count)}
                </div>
              </div>
            ))}
          </div>
        </section>

        {intentEntries.map(([mode, count]) => (
          <section
            key={mode}
            className="card-surface col-span-12 flex flex-col gap-4 rounded-lg p-6 lg:col-span-6"
          >
            <h3 className="flex items-center justify-between gap-2 font-headline-md text-headline-md text-on-surface">
              <span className="min-w-0 break-words">Intent mode · {mode}</span>
              <Icon name={mode === "stall" ? "pause" : "bookmark"} className="text-primary" />
            </h3>
            <div className="flex flex-1 flex-col items-center justify-center rounded-md bg-level-0 p-4">
              <div className="font-display text-[48px] font-semibold tnum text-on-surface">
                {formatInteger(count)}
              </div>
              <p className="mt-2 max-w-[260px] text-center font-body-md text-body-md text-on-surface-variant">
                Count from the Query API. Bookmark and stall stay separate.
              </p>
            </div>
          </section>
        ))}

        <section className="card-surface col-span-12 rounded-lg">
          <div className="p-4 hairline-b">
            <h2 className="font-headline-md text-headline-md text-on-surface">Intent tags</h2>
          </div>
          <div className="space-y-3 p-6">
            {tagEntries.length ? (
              tagEntries.map(([tag, count]) => (
                <div key={tag} className="flex min-w-0 items-center gap-3">
                  <div className="w-24 shrink-0 truncate text-right font-label-md text-[12px] text-on-surface sm:w-40">
                    {tag}
                  </div>
                  <div className="h-4 min-w-0 flex-1 overflow-hidden rounded-sm bg-level-0">
                    <div
                      className="h-full bg-tertiary"
                      style={{ width: barWidth(count, tagPeak) }}
                    />
                  </div>
                  <div className="w-16 font-number-data text-[12px] tnum">{formatInteger(count)}</div>
                </div>
              ))
            ) : (
              <p className="font-body-md text-on-surface-variant">No intent tags in this slice.</p>
            )}
          </div>
        </section>

        <section className="card-surface col-span-12 rounded-lg">
          <div className="flex items-center justify-between p-4 hairline-b">
            <h2 className="font-headline-md text-headline-md text-on-surface">
              Top opportunity areas
            </h2>
            <Link
              href={withCurrentFilters("/themes", searchParams.toString())}
              className="font-label-md text-label-md text-primary hover:underline"
            >
              View all
            </Link>
          </div>
          <div className="hidden grid-cols-[3.5rem_minmax(0,1fr)_5.5rem_4.5rem_8rem] gap-4 bg-level-0 px-6 py-3 font-label-md text-label-md text-on-surface-variant hairline-b md:grid">
            <div>Rank</div>
            <div>Theme</div>
            <div>Mentions</div>
            <div>SoV</div>
            <div>Trend</div>
          </div>
          {topThemes.length ? (
            topThemes.map((theme) => (
              <button
                key={theme.theme_id}
                type="button"
                className="grid w-full min-w-0 grid-cols-2 items-center gap-x-4 gap-y-1 px-4 py-4 text-left hairline-b hover:bg-level-0 sm:px-6 md:grid-cols-[3.5rem_minmax(0,1fr)_5.5rem_4.5rem_8rem]"
                onClick={() => {
                  setFilters({ theme_id: theme.theme_id });
                }}
              >
                <div className="font-number-data font-medium tnum">{rankLabel(theme.rank)}</div>
                <div className="min-w-0 truncate font-body-md font-medium text-on-surface">{theme.name}</div>
                <div className="font-number-data text-on-surface-variant tnum">
                  {formatInteger(theme.mention_count)}
                </div>
                <div className="font-number-data tnum">
                  {formatShareOfVoice(theme.share_of_voice)}
                </div>
                <div className="col-span-2 font-label-md text-on-surface-variant md:col-span-1">
                  {theme.sparkline_insufficient
                    ? "insufficient history"
                    : theme.trend_direction ?? "—"}
                </div>
              </button>
            ))
          ) : (
            <p className="px-6 py-8 font-body-md text-on-surface-variant">No published themes yet.</p>
          )}
        </section>
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  suffix,
}: {
  label: string;
  value: string;
  suffix?: string;
}) {
  return (
    <div className="card-surface flex h-[120px] min-w-0 flex-col justify-between rounded-lg p-6">
      <div className="font-label-md text-label-md text-on-surface-variant">{label}</div>
      <div className="flex items-baseline gap-2 font-display text-display tnum text-on-surface">
        {value}
        {suffix ? (
          <span className="font-headline-md text-headline-md text-on-surface-variant">{suffix}</span>
        ) : null}
      </div>
    </div>
  );
}

function barWidth(value: number, peak: number): string {
  if (peak <= 0) return "0%";
  return `${Math.min(100, (value / peak) * 100)}%`;
}
