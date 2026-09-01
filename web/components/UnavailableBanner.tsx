"use client";

import { Icon } from "@/components/Icon";
import { OUT_OF_SCOPE_SOURCES, SOURCE_LABELS } from "@/lib/constants";
import { formatDateTime } from "@/lib/format";
import type { OverviewResponse } from "@/lib/types";

const OUT_OF_SCOPE = new Set<string>(OUT_OF_SCOPE_SOURCES);

export function UnavailableBanner({ overview }: { overview?: OverviewResponse }) {
  if (!overview) return null;
  const failed = overview.counts_by_source.filter(
    (row) => row.last_run_status === "failed",
  );
  const play = overview.counts_by_source.find((row) => row.source_type === "play_store");
  const playFailed = play?.last_run_status === "failed";
  // Store connectors are required for the dashboard. Reddit 403 / missing YouTube
  // keys / ToS-unavailable catalogs belong on Sources, not as an Overview outage.
  const blocking = failed.filter(
    (row) => row.source_type === "play_store" || row.source_type === "app_store",
  );

  if (!blocking.length) return null;

  const names = blocking.map((row) => SOURCE_LABELS[row.source_type] ?? row.source_type);

  return (
    <div className="card-surface flex min-w-0 items-start gap-3 rounded-lg p-4">
      <Icon name="error" className="shrink-0 text-error" />
      <div className="min-w-0 flex-1">
        <h4 className="font-number-data text-number-data font-semibold text-on-surface">
          {playFailed ? "Play Store ingest failure" : "Connector ingest failed"}
        </h4>
        <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
          {names.join(", ")} is not live. Counts are not interpolated.
          {OUT_OF_SCOPE.size
            ? " Out-of-scope platforms (Instagram, Facebook, on-site Myntra) stay on Sources — they are not missing backend volume."
            : null}
          {playFailed && play?.last_successful_pull
            ? ` Last successful Play pull: ${formatDateTime(play.last_successful_pull)}.`
            : null}
        </p>
      </div>
    </div>
  );
}
