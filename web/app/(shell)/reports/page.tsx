"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Icon } from "@/components/Icon";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { downloadReportPdf, fetchReportJson, fetchReports } from "@/lib/api";
import { formatDateTime, formatInteger, formatShareOfVoice } from "@/lib/format";
import type { ReportListItem } from "@/lib/types";

export default function ReportsPage() {
  const reports = useQuery({ queryKey: ["reports"], queryFn: fetchReports });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);

  const list = reports.data?.reports ?? [];
  const selected: ReportListItem | undefined =
    list.find((item) => item.id === selectedId) ?? list[0];
  const activeId = selected?.id ?? null;

  const detail = useQuery({
    queryKey: ["report", activeId],
    queryFn: () => fetchReportJson(activeId as string),
    enabled: Boolean(activeId),
  });

  const narrative = detail.data?.narrative || selected?.narrative || "";
  const topThemes = useMemo(() => {
    if (detail.data?.top_themes?.length) return detail.data.top_themes;
    if (selected?.top_themes?.length) return selected.top_themes;
    const diff = detail.data?.diff as { top_themes?: ReportListItem["top_themes"] } | undefined;
    return diff?.top_themes ?? [];
  }, [selected, detail.data]);

  if (reports.isLoading) return <PageSkeleton />;
  if (reports.error) {
    return <ErrorState message={reports.error.message} onRetry={() => reports.refetch()} />;
  }

  const header = selected?.header ?? {};
  const corpus = header.corpus_size;
  const period =
    selected?.period_start && selected?.period_end
      ? `${String(selected.period_start).slice(0, 10)} – ${String(selected.period_end).slice(0, 10)}`
      : "Current snapshot";

  return (
    <div className="space-y-8">
      <div className="card-surface flex min-w-0 items-start gap-4 rounded-xl p-4">
        <Icon name="info" className="mt-0.5 text-secondary" />
        <p className="font-body-md text-body-md text-on-surface-variant">
          Weekly snapshot of opportunity areas from public reviews. Findings are stated user
          language, not proven causal drop-off.
        </p>
      </div>
      {reports.data?.empty || !list.length ? (
        <EmptyState
          title="No reports yet"
          body="Theme metrics are required before a weekly report can be written."
        />
      ) : (
        <div className="grid grid-cols-1 gap-8 xl:grid-cols-12">
          <section className="card-surface min-w-0 overflow-hidden rounded-xl xl:col-span-5">
            <div className="bg-surface-container-lowest p-card hairline-b">
              <h2 className="font-headline-md text-headline-md">Weekly summaries</h2>
            </div>
            <div className="divide-y divide-outline-variant">
              {list.map((report) => (
                <div
                  key={report.id}
                  className={`flex min-w-0 cursor-pointer items-center justify-between gap-3 p-4 hover:bg-level-0 ${
                    activeId === report.id
                      ? "border-l-4 border-primary bg-level-0"
                      : "border-l-4 border-transparent"
                  }`}
                  onClick={() => {
                    setPdfError(null);
                    setSelectedId(report.id);
                  }}
                >
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-label-md font-semibold text-on-surface">
                      {report.title}
                    </span>
                    <span className="font-body-md text-[12px] text-on-surface-variant">
                      {formatDateTime(report.created_at)}
                    </span>
                  </div>
                  <span className="rounded-full bg-secondary-container px-2 py-0.5 font-label-md text-[10px] text-on-secondary-container">
                    {report.status}
                  </span>
                </div>
              ))}
            </div>
          </section>
          <section className="xl:col-span-7">
            {selected ? (
              <div className="card-surface space-y-6 rounded-xl p-card">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="font-headline-md text-headline-md">{selected.title}</h3>
                    <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
                      {period}
                      {corpus != null ? ` · eligible corpus ${String(corpus)}` : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="flex items-center gap-1 rounded-full border border-outline-variant px-3 py-1 font-label-md text-label-md hover:bg-surface-container"
                    onClick={() => {
                      setPdfError(null);
                      void downloadReportPdf(selected.id).catch((error: unknown) => {
                        setPdfError(error instanceof Error ? error.message : "PDF download failed");
                      });
                    }}
                  >
                    <Icon name="download" className="text-[16px]" />
                    Download PDF
                  </button>
                </div>
                {pdfError ? (
                  <p className="font-body-md text-error">{pdfError}</p>
                ) : null}
                <div>
                  <h4 className="mb-2 font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                    Summary
                  </h4>
                  <p className="whitespace-pre-wrap font-body-lg text-body-lg text-on-surface">
                    {detail.isLoading && !narrative
                      ? "Loading narrative…"
                      : narrative || "Snapshot is ready. Top opportunity areas are listed below."}
                  </p>
                </div>
                {topThemes.length ? (
                  <div>
                    <h4 className="mb-3 font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                      Top opportunity areas
                    </h4>
                    <div className="space-y-2">
                      {topThemes.map((theme) => (
                        <div
                          key={String(theme.theme_id ?? theme.name)}
                          className="flex items-center justify-between gap-3 rounded-lg border border-outline-variant px-4 py-3"
                        >
                          <span className="font-body-md font-medium text-on-surface">
                            {String(theme.name ?? "Theme")}
                          </span>
                          <span className="font-number-data text-on-surface-variant tnum">
                            {typeof theme.mention_count === "number"
                              ? formatInteger(theme.mention_count)
                              : "—"}{" "}
                            mentions
                            {typeof theme.share_of_voice === "number"
                              ? ` · ${formatShareOfVoice(theme.share_of_voice)}`
                              : ""}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>
        </div>
      )}
    </div>
  );
}
