"use client";

import { Heatmap } from "@/components/Heatmap";
import { Icon } from "@/components/Icon";
import { LiveBadge } from "@/components/Chips";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { SOURCE_ICONS, SOURCE_LABELS } from "@/lib/constants";
import { useFilters } from "@/lib/filters";
import { formatDateTime, formatInteger, sourceLabel } from "@/lib/format";
import { useOverviewQuery, useSegmentsQuery } from "@/lib/hooks";
import { ingestedSourceRows } from "@/lib/sources";
import type { SegmentCell } from "@/lib/types";

export default function SourcesPage() {
  const { filters, setFilters, setFilter } = useFilters();
  const overview = useOverviewQuery(filters);
  const mix = useSegmentsQuery(filters, "source_type");

  if (overview.isPending && !overview.data) return <PageSkeleton />;
  if (overview.error) {
    return <ErrorState message={overview.error.message} onRetry={() => overview.refetch()} />;
  }
  const data = overview.data;
  if (!data) return null;

  function onCell(cell: SegmentCell) {
    setFilters({ theme_id: cell.theme_id, source_type: cell.segment });
  }

  const sourceRows = ingestedSourceRows(data.counts_by_source);

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-headline-lg text-headline-lg text-on-surface">Data sources</h2>
        <p className="mt-1 font-body-md text-on-surface-variant">
          Only sources with ingested documents are listed. Volume is never interpolated for
          platforms that were not pulled.
        </p>
      </div>

      <section className="card-surface rounded-xl p-card">
        <h3 className="font-headline-md text-headline-md">Theme mix by source</h3>
        <p className="mb-6 mt-1 font-label-md text-label-md text-on-surface-variant">
          Each cell is mention_count for that theme on that source_type.
        </p>
        {mix.data && !mix.data.empty ? (
          <Heatmap
            cells={mix.data.cells}
            unknownVisible={mix.data.unknown_visible}
            onSelect={onCell}
          />
        ) : (
          <EmptyState title="No source mix yet" body="Cluster metrics have no source_type slices." />
        )}
      </section>

      <section>
        <div className="mb-6 flex items-center gap-2">
          <h3 className="font-headline-md text-headline-md">Configured endpoints</h3>
          <span className="rounded-full bg-surface-variant px-2 py-0.5 font-number-data text-[12px]">
            {sourceRows.length}
          </span>
        </div>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
          {sourceRows.map((row) => {
            const live = row.status === "live";
            return (
              <article
                key={row.source_type}
                className={
                  live
                    ? "card-surface flex flex-col gap-5 rounded-xl p-card"
                    : "flex flex-col gap-5 rounded-xl border-2 border-dashed border-outline-variant bg-surface-container-low p-card opacity-90"
                }
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-outline-variant bg-surface text-primary">
                      <Icon name={SOURCE_ICONS[row.source_type] ?? "source"} />
                    </div>
                    <div>
                      <h4 className="font-headline-md text-headline-md leading-tight">
                        {sourceLabel(row.source_type, SOURCE_LABELS)}
                      </h4>
                      <p className="mt-0.5 font-label-md text-label-md text-on-surface-variant">
                        {row.source_type}
                      </p>
                    </div>
                  </div>
                  {live ? <LiveBadge /> : null}
                </div>
                <div className="grid grid-cols-2 gap-4 border-y border-outline-variant py-4">
                  <div>
                    <p className="font-label-md text-[10px] uppercase tracking-wider text-on-surface-variant">
                      Eligible in slice
                    </p>
                    <p className="mt-1 font-number-data text-[20px] font-semibold tnum">
                      {formatInteger(row.eligible_count)}
                    </p>
                    {!row.volume_is_current ? (
                      <p className="mt-1 font-label-md text-[11px] text-error">
                        Volume is not from a current live pull
                      </p>
                    ) : null}
                  </div>
                  <div>
                    <p className="font-label-md text-[10px] uppercase tracking-wider text-on-surface-variant">
                      Last successful pull
                    </p>
                    <p className="mt-1 font-body-md font-medium">
                      {formatDateTime(row.last_successful_pull)}
                    </p>
                  </div>
                </div>
                <p className="font-body-md text-on-surface-variant">
                  Normalized {formatInteger(row.normalized_count)} · raw{" "}
                  {formatInteger(row.raw_count)}
                  {row.notes ? ` · ${row.notes}` : ""}
                </p>
                <button
                  type="button"
                  className="self-start rounded-md border border-hairline px-3 py-1.5 font-label-md text-label-md hover:bg-surface-variant"
                  onClick={() => setFilter("source_type", row.source_type)}
                >
                  Filter to this source
                </button>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
