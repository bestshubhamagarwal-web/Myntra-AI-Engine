"use client";

import { useEvidenceQuery } from "@/lib/hooks";
import { downloadEvidenceCsv } from "@/lib/api";
import { SOURCE_LABELS } from "@/lib/constants";
import { formatDate, sourceLabel } from "@/lib/format";
import type { Citation, EvidenceRow } from "@/lib/types";
import type { FilterState } from "@/lib/filters";

import { Icon } from "./Icon";
import { IntentChip } from "./Chips";

export function EvidenceDrawer({
  open,
  documentId,
  chunkId,
  filters,
  seed,
  onClose,
}: {
  open: boolean;
  documentId?: string;
  chunkId?: string;
  filters: FilterState;
  seed?: Citation | EvidenceRow | null;
  onClose: () => void;
}) {
  const evidence = useEvidenceQuery(filters);
  const rows = (evidence.data?.rows ?? []).filter((row) =>
    documentId ? row.document_id === documentId : true,
  );
  const shown = rows.length ? rows : seed ? [citationAsRow(seed, documentId)] : [];

  return (
    <>
      <div
        className={`fixed inset-0 z-40 bg-inverse-surface/30 backdrop-blur-sm transition-opacity ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
      />
      <aside
        className={`fixed right-0 top-0 z-50 flex h-screen w-full max-w-[480px] flex-col border-l border-outline-variant bg-surface p-card shadow-lift transition-transform duration-300 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        aria-hidden={!open}
      >
        <div className="mb-6 flex items-start justify-between border-b border-outline-variant pb-4">
          <div>
            <h2 className="font-headline-md text-headline-md font-semibold text-primary">Evidence</h2>
            <p className="font-body-md text-body-md text-on-surface-variant">
              Verbatim quotes and source links
            </p>
          </div>
          <button
            type="button"
            className="focus-ring rounded-full p-2 text-on-surface-variant hover:bg-surface-variant"
            onClick={onClose}
            aria-label="Close evidence drawer"
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="flex-1 space-y-6 overflow-y-auto pr-1">
          {evidence.isLoading && !shown.length ? (
            <p className="font-body-md text-on-surface-variant">Loading quotes…</p>
          ) : null}
          {!shown.length && !evidence.isLoading ? (
            <p className="font-body-md text-on-surface-variant">
              No quotes for this document in the current filter slice.
            </p>
          ) : null}
          {shown.map((row) => (
            <article
              key={`${row.document_id}:${row.chunk_id}:${row.quote.slice(0, 24)}`}
              className="rounded-lg border border-outline-variant bg-surface-container-lowest p-4"
            >
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Icon name="format_quote" className="text-[18px] text-primary" />
                  <span className="rounded bg-surface-variant px-2 py-0.5 text-xs font-medium text-on-surface-variant">
                    {sourceLabel(row.source_type, SOURCE_LABELS)}
                  </span>
                  {row.intent_mode ? <IntentChip mode={row.intent_mode} /> : null}
                </div>
                <span className="text-xs text-on-surface-variant">{formatDate(row.published_at)}</span>
              </div>
              <p className="mb-3 font-body-lg text-body-lg leading-relaxed text-on-surface">
                “{row.quote}”
              </p>
              <div className="flex flex-wrap gap-1.5">
                {row.friction_tags?.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-sm bg-secondary-container px-2 py-0.5 font-label-md text-[11px] text-on-secondary-container"
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <div className="mt-3">
                {row.link_unavailable || !row.url ? (
                  <span className="inline-flex items-center gap-1 font-label-md text-label-md text-on-surface-variant">
                    <Icon name="link_off" className="text-[16px]" />
                    Link unavailable
                  </span>
                ) : (
                  <a
                    href={row.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 font-label-md text-label-md text-primary hover:underline"
                  >
                    View original
                    <Icon name="open_in_new" className="text-[14px]" />
                  </a>
                )}
              </div>
              <p className="mt-2 font-label-md text-[10px] text-on-surface-variant">
                document_id {row.document_id}
                {chunkId ? ` · chunk ${chunkId}` : row.chunk_id ? ` · chunk ${row.chunk_id}` : ""}
              </p>
            </article>
          ))}
        </div>
        <div className="mt-4 flex justify-end border-t border-outline-variant pt-4">
          <button
            type="button"
            className="focus-ring rounded border border-outline-variant bg-surface-container-high px-4 py-2 font-label-md text-label-md text-on-surface hover:bg-surface-variant"
            onClick={() => downloadEvidenceCsv(filters)}
          >
            Export evidence CSV
          </button>
        </div>
      </aside>
    </>
  );
}

function citationAsRow(
  seed: Citation | EvidenceRow,
  documentId?: string,
): EvidenceRow {
  if ("quote" in seed && "source_type" in seed && "document_id" in seed && "link_unavailable" in seed) {
    return seed as EvidenceRow;
  }
  const citation = seed as Citation;
  return {
    document_id: citation.document_id || documentId || "",
    chunk_id: citation.chunk_id,
    theme_id: null,
    theme_name: null,
    quote: citation.quote,
    source_type: citation.source_type,
    url: citation.url,
    link_unavailable: !citation.url,
    published_at: citation.published_at,
    product_category: null,
    intent_mode: null,
    intent_tag: null,
    friction_tags: [],
    sentiment: null,
    maps_to_questions: [],
  };
}
