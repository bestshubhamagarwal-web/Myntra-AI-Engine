"use client";

import { Heatmap } from "@/components/Heatmap";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { useFilters } from "@/lib/filters";
import { formatInteger, formatShareOfVoice } from "@/lib/format";
import { useEvidenceQuery, useSegmentsQuery } from "@/lib/hooks";
import type { SegmentCell } from "@/lib/types";

export default function CategoriesPage() {
  const { filters, setFilters, openDrawer } = useFilters();
  const segments = useSegmentsQuery(filters, "product_category");
  const evidence = useEvidenceQuery(filters);

  if (segments.isPending && !segments.data) return <PageSkeleton />;
  if (segments.error) {
    return <ErrorState message={segments.error.message} onRetry={() => segments.refetch()} />;
  }
  const data = segments.data;
  if (!data) return null;

  function drill(cell: SegmentCell) {
    setFilters({
      theme_id: cell.theme_id,
      product_category: cell.segment === "unknown" ? "unknown" : cell.segment,
    });
    const row = evidence.data?.rows.find((item) => item.theme_id === cell.theme_id);
    if (row) openDrawer(row.document_id, row.chunk_id);
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-headline-lg text-headline-lg text-on-surface">Category distribution</h2>
        <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
          Theme × product_category cells from the Query API. Unknown is always shown. Click a cell
          to open evidence. Small-n cells are outlined.
        </p>
      </div>
      {data.empty ? (
        <EmptyState
          title="No category slices"
          body="There are no category cells for these filters."
        />
      ) : (
        <>
          <section className="card-surface rounded-xl p-card">
            <h3 className="mb-6 font-headline-md text-headline-md">Volume by theme and category</h3>
            <Heatmap cells={data.cells} unknownVisible={data.unknown_visible} onSelect={drill} />
            <p className="mt-4 font-label-md text-label-md text-on-surface-variant">
              Cell values are mention_count from the API. Color is display scaling only.
            </p>
          </section>
          <section className="card-surface rounded-xl p-card">
            <h3 className="mb-4 font-headline-md text-headline-md">Category details</h3>
            <div className="w-full min-w-0 overflow-hidden">
              <table className="w-full table-fixed text-left">
                <thead>
                  <tr className="border-b border-hairline font-label-md text-label-md text-on-surface-variant">
                    <th className="w-[22%] px-3 py-3 sm:px-4">Category</th>
                    <th className="w-[28%] px-3 py-3 sm:px-4">Theme</th>
                    <th className="w-[16%] px-3 py-3 sm:px-4">Mentions</th>
                    <th className="w-[14%] px-3 py-3 sm:px-4">SoV</th>
                    <th className="w-[20%] px-3 py-3 sm:px-4">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {data.cells.map((cell) => (
                    <tr
                      key={`${cell.theme_id}:${cell.segment}`}
                      className="cursor-pointer border-b border-hairline hover:bg-level-0"
                      onClick={() => drill(cell)}
                    >
                      <td className="break-words px-3 py-4 font-medium sm:px-4">{cell.segment}</td>
                      <td className="break-words px-3 py-4 sm:px-4">{cell.theme_name}</td>
                      <td className="px-3 py-4 font-number-data tnum sm:px-4">
                        {formatInteger(cell.mention_count)}
                      </td>
                      <td className="px-3 py-4 font-number-data tnum sm:px-4">
                        {formatShareOfVoice(cell.share_of_voice)}
                      </td>
                      <td className="break-words px-3 py-4 font-body-md text-on-surface-variant sm:px-4">
                        {cell.small_n ? cell.caveat : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
