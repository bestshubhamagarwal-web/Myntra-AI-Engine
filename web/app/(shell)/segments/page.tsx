"use client";

import { Heatmap } from "@/components/Heatmap";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { SEGMENT_DIMENSIONS } from "@/lib/constants";
import { useFilters } from "@/lib/filters";
import { useEvidenceQuery, useSegmentsQuery } from "@/lib/hooks";
import type { SegmentCell } from "@/lib/types";

const LABELS: Record<string, string> = {
  product_category: "Category",
  source_type: "Source",
  price_tier: "Price tier",
  platform_used: "Platform",
  gender_segment: "Gender",
};

export default function SegmentsPage() {
  const { filters, setFilters, openDrawer, searchParams } = useFilters();
  const dimension = searchParams.get("dimension") || "product_category";
  const segments = useSegmentsQuery(filters, dimension);
  const evidence = useEvidenceQuery(filters);

  if (segments.isPending && !segments.data) return <PageSkeleton />;
  if (segments.error) {
    return <ErrorState message={segments.error.message} onRetry={() => segments.refetch()} />;
  }
  const data = segments.data;
  if (!data) return null;

  function drill(cell: SegmentCell) {
    const patch: Record<string, string> = { theme_id: cell.theme_id };
    if (dimension === "product_category") patch.product_category = cell.segment;
    if (dimension === "source_type") patch.source_type = cell.segment;
    if (dimension === "gender_segment") patch.gender_segment = cell.segment;
    if (dimension === "price_tier") patch.price_tier = cell.segment;
    if (dimension === "platform_used") patch.platform_used = cell.segment;
    setFilters(patch);
    const row = evidence.data?.rows.find((item) => item.theme_id === cell.theme_id);
    if (row) openDrawer(row.document_id, row.chunk_id);
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <div>
          <h2 className="mb-2 font-headline-lg text-headline-lg text-on-surface">
            Segment opportunities
          </h2>
          <p className="max-w-2xl font-body-md text-body-md text-on-surface-variant">
            Cross-tab of themes × segments. The unknown column is always present. Small-n cells
            are dashed and should not be read as a majority of users.
          </p>
        </div>
        <div className="flex flex-wrap rounded-lg border border-outline-variant bg-surface-container-low p-1">
          {SEGMENT_DIMENSIONS.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setFilters({ dimension: item })}
              className={
                dimension === item
                  ? "rounded-md border border-outline-variant bg-surface px-4 py-1.5 font-label-md text-label-md text-on-surface"
                  : "rounded-md px-4 py-1.5 font-label-md text-label-md text-on-surface-variant"
              }
            >
              {LABELS[item]}
            </button>
          ))}
        </div>
      </div>
      {data.empty ? (
        <EmptyState title="No segment cells" body="No theme × segment snapshot for this slice." />
      ) : (
        <section className="card-surface rounded-xl p-card">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant pb-4">
            <span className="font-body-md font-medium">
              Metric: mention_count (API) · {LABELS[dimension]}
            </span>
            <span className="font-label-md text-label-md text-on-surface-variant">
              small-n threshold {data.small_n_threshold}
            </span>
          </div>
          <Heatmap cells={data.cells} unknownVisible={data.unknown_visible} onSelect={drill} />
        </section>
      )}
    </div>
  );
}
