"use client";

import { PhraseCloud } from "@/components/PhraseCloud";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { Icon } from "@/components/Icon";
import { useFilters } from "@/lib/filters";
import { formatInteger } from "@/lib/format";
import { useNgramsQuery, useThemesQuery } from "@/lib/hooks";

export default function PhrasesPage() {
  const { filters, setFilter, setFilters } = useFilters();
  const ngrams = useNgramsQuery(filters);
  const themes = useThemesQuery(filters);

  if (ngrams.isPending && !ngrams.data) return <PageSkeleton />;
  if (ngrams.error) {
    return <ErrorState message={ngrams.error.message} onRetry={() => ngrams.refetch()} />;
  }
  const data = ngrams.data;
  if (!data) return null;

  return (
    <div className="flex min-w-0 flex-col gap-6 lg:h-full lg:flex-row lg:gap-8">
      <section className="card-surface flex min-h-[28rem] min-w-0 w-full flex-col overflow-hidden rounded-lg lg:w-1/2">
        <div className="flex items-center justify-between bg-surface-container-lowest p-4 hairline-b">
          <h2 className="font-headline-md text-headline-md">N-gram frequency</h2>
        </div>
        {data.empty ? (
          <div className="p-6">
            <EmptyState title="No n-grams" body="Run the n-gram job or pick another slice." />
          </div>
        ) : (
          <div className="flex-1 overflow-auto bg-level-0">
            <table className="w-full table-fixed text-left">
              <thead className="sticky top-0 bg-surface-container-low font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                <tr>
                  <th className="p-3 font-medium sm:p-4">Phrase</th>
                  <th className="w-16 p-3 text-right font-medium sm:w-24 sm:p-4">Count</th>
                  <th className="w-20 p-3 font-medium sm:w-32 sm:p-4">Sentiment</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline bg-surface">
                {data.rows.map((row) => (
                  <tr
                    key={`${row.n}:${row.gram}:${row.theme_id ?? ""}:${row.category ?? ""}`}
                    className="cursor-pointer hover:bg-level-0"
                    onClick={() => {
                      if (row.theme_id) setFilter("theme_id", row.theme_id);
                      if (row.category) setFilter("product_category", row.category);
                    }}
                  >
                    <td className="max-w-0 p-3 font-number-data font-medium sm:p-4">
                      <span className="block truncate" title={row.gram}>
                        {row.gram}
                      </span>
                    </td>
                    <td className="p-3 text-right font-number-data tnum text-on-surface-variant sm:p-4">
                      {formatInteger(row.count)}
                    </td>
                    <td className="truncate p-3 font-label-md text-[11px] text-on-surface-variant sm:p-4">
                      {row.sentiment || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card-surface flex min-h-[28rem] min-w-0 w-full flex-col overflow-hidden rounded-lg lg:w-1/2">
        <div className="flex flex-wrap items-center justify-between gap-2 bg-surface-container-lowest p-4 hairline-b">
          <h2 className="flex items-center gap-2 font-headline-md text-headline-md">
            Filtered phrase cloud
            {filters.theme_id || filters.product_category ? (
              <span className="min-w-0 truncate font-body-md text-body-md text-on-surface-variant">
                {themes.data?.themes.find((t) => t.theme_id === filters.theme_id)?.name ||
                  filters.product_category}
              </span>
            ) : null}
          </h2>
          {filters.theme_id || filters.product_category ? (
            <button
              type="button"
              className="font-label-md text-label-md text-primary"
              onClick={() => setFilters({ theme_id: null, product_category: null })}
            >
              Clear filter
            </button>
          ) : null}
        </div>
        <div className="flex flex-1 items-center justify-center bg-level-0">
          {data.cloud_eligible ? (
            data.rows.length ? (
              <PhraseCloud rows={data.rows} />
            ) : (
              <EmptyState title="No phrases" body="This theme or category has no n-grams." />
            )
          ) : (
            <div className="flex max-w-xs flex-col items-center text-center text-on-surface-variant">
              <Icon name="cloud_off" className="mb-4 text-[48px] opacity-50" />
              <p className="font-body-lg text-body-lg">
                Select a theme or category to generate a filtered phrase cloud. The table stays
                available without that filter.
              </p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
