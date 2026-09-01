"use client";

import { useState } from "react";

import { IntentChip } from "@/components/Chips";
import { Icon } from "@/components/Icon";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { downloadEvidenceCsv } from "@/lib/api";
import { SOURCE_LABELS } from "@/lib/constants";
import { useFilters } from "@/lib/filters";
import { formatDate, sourceLabel } from "@/lib/format";
import { useEvidenceQuery } from "@/lib/hooks";

export default function EvidencePage() {
  const { filters, setFilter, openDrawer } = useFilters();
  const evidence = useEvidenceQuery(filters);
  const [draft, setDraft] = useState(filters.q ?? "");

  if (evidence.isPending && !evidence.data) return <PageSkeleton />;
  if (evidence.error) {
    return <ErrorState message={evidence.error.message} onRetry={() => evidence.refetch()} />;
  }
  const data = evidence.data;
  if (!data) return null;

  return (
    <div className="flex min-h-[calc(100vh-8rem)] min-w-0 flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="relative w-full max-w-md">
          <Icon
            name="search"
            className="absolute left-3 top-1/2 -translate-y-1/2 text-[18px] text-on-surface-variant"
          />
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") setFilter("q", draft || null);
            }}
            placeholder="Search verbatim, keywords, or source…"
            className="focus-ring w-full rounded-md border border-outline-variant bg-surface py-2 pl-10 pr-4 font-body-md text-body-md"
          />
        </div>
        <button
          type="button"
          onClick={() => downloadEvidenceCsv(filters)}
          className="rounded-md border border-hairline px-4 py-2 font-label-md text-label-md text-on-surface hover:bg-surface-container-low"
        >
          Export scrubbed CSV
        </button>
      </div>

      {data.empty ? (
        <EmptyState
          title="No evidence in this slice"
          body="These filters match no quotes. The table is empty on purpose — a previous result set is not kept on screen."
        />
      ) : (
        <div className="card-surface flex min-w-0 flex-1 flex-col overflow-hidden rounded-md">
          <div className="min-w-0">
            <div className="hidden grid-cols-[6.5rem_7.5rem_minmax(0,1fr)_8rem_6.5rem_2.5rem] gap-3 border-b border-outline-variant bg-surface-container-lowest px-4 py-3 font-label-md text-label-md text-on-surface-variant xl:grid">
              <div>Date</div>
              <div>Source</div>
              <div>Quote</div>
              <div>Theme</div>
              <div>Intent</div>
              <div className="text-center">Link</div>
            </div>
            {data.rows.map((row) => (
              <div
                key={`${row.document_id}:${row.quote.slice(0, 32)}`}
                role="button"
                tabIndex={0}
                className="grid w-full min-w-0 cursor-pointer grid-cols-1 gap-2 border-b border-outline-variant px-4 py-3 text-left hover:bg-surface-container-low sm:grid-cols-[7.5rem_minmax(0,1fr)] xl:grid-cols-[6.5rem_7.5rem_minmax(0,1fr)_8rem_6.5rem_2.5rem] xl:items-center xl:gap-3"
                onClick={() => openDrawer(row.document_id, row.chunk_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openDrawer(row.document_id, row.chunk_id);
                  }
                }}
              >
                <div className="font-body-md text-on-surface-variant">
                  {formatDate(row.published_at)}
                </div>
                <div className="truncate font-body-md">
                  {sourceLabel(row.source_type, SOURCE_LABELS)}
                </div>
                <div className="min-w-0 sm:col-span-2 xl:col-span-1">
                  <p className="line-clamp-2 font-body-md leading-snug">{row.quote}</p>
                  <p className="mt-1 font-label-md text-label-md text-on-surface-variant xl:hidden">
                    {[row.product_category || "unknown", row.friction_tags.join(", ") || null]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <div className="hidden min-w-0 xl:block">
                  {row.theme_name ? (
                    <span className="line-clamp-2 rounded-sm bg-secondary-container px-2.5 py-1 font-label-md text-[11px] text-on-secondary-container">
                      {row.theme_name}
                    </span>
                  ) : (
                    "—"
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {row.theme_name ? (
                    <span className="rounded-sm bg-secondary-container px-2.5 py-1 font-label-md text-[11px] text-on-secondary-container xl:hidden">
                      {row.theme_name}
                    </span>
                  ) : null}
                  <IntentChip mode={row.intent_mode} />
                  {row.link_unavailable || !row.url ? (
                    <span title="Link unavailable" className="xl:hidden">
                      <Icon name="link_off" className="text-outline" />
                    </span>
                  ) : (
                    <a
                      href={row.url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="text-on-surface-variant hover:text-primary xl:hidden"
                    >
                      <Icon name="open_in_new" />
                    </a>
                  )}
                </div>
                <div className="hidden text-center xl:block">
                  {row.link_unavailable || !row.url ? (
                    <span title="Link unavailable">
                      <Icon name="link_off" className="text-outline" />
                    </span>
                  ) : (
                    <a
                      href={row.url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="text-on-surface-variant hover:text-primary"
                    >
                      <Icon name="open_in_new" />
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between border-t border-outline-variant p-4">
            <span className="font-label-md text-label-md text-on-surface-variant">
              Showing {data.rows.length} quotes (scrubbed)
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
