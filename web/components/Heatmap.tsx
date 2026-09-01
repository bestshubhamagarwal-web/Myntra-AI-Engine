"use client";

import { cn } from "@/lib/cn";
import { formatInteger, formatShareOfVoice } from "@/lib/format";
import type { SegmentCell } from "@/lib/types";

function uniquePreserve(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    if (seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

function heatClass(mentionCount: number, peak: number): string {
  if (peak <= 0 || mentionCount <= 0) return "bg-level-0 text-on-surface-variant";
  const ratio = mentionCount / peak;
  if (ratio >= 0.8) return "bg-primary text-on-primary";
  if (ratio >= 0.6) return "bg-primary-container text-on-primary";
  if (ratio >= 0.4) return "bg-primary-fixed text-on-primary-fixed";
  if (ratio >= 0.2) return "bg-secondary-container text-on-secondary-container";
  return "bg-surface-container-high text-on-surface";
}

export function Heatmap({
  cells,
  unknownVisible,
  onSelect,
}: {
  cells: SegmentCell[];
  unknownVisible: boolean;
  onSelect?: (cell: SegmentCell) => void;
}) {
  const themeIds = uniquePreserve(cells.map((c) => c.theme_id));
  const nameById = new Map(cells.map((c) => [c.theme_id, c.theme_name]));
  let segments = uniquePreserve(cells.map((c) => c.segment));
  if (unknownVisible && !segments.includes("unknown")) {
    segments = [...segments, "unknown"];
  }

  const lookup = new Map<string, SegmentCell>();
  for (const cell of cells) {
    lookup.set(`${cell.theme_id}::${cell.segment}`, cell);
  }

  let peak = 0;
  for (const cell of cells) {
    if (cell.mention_count > peak) peak = cell.mention_count;
  }

  if (!themeIds.length) {
    return null;
  }

  return (
    <div className="w-full min-w-0">
      <table className="w-full table-fixed border-separate border-spacing-1">
        <thead>
          <tr>
            <th className="w-[28%] pb-2 pl-2 text-left font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
              Opportunity theme
            </th>
            {segments.map((segment) => (
              <th
                key={segment}
                className="break-words pb-2 text-center font-label-md text-[11px] font-medium uppercase tracking-wider text-on-surface-variant"
              >
                {segment}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {themeIds.map((themeId) => (
            <tr key={themeId}>
              <th className="break-words px-2 py-1 text-left font-body-md text-body-md font-medium text-on-surface">
                {nameById.get(themeId) ?? themeId}
              </th>
              {segments.map((segment) => {
                const cell = lookup.get(`${themeId}::${segment}`);
                  if (!cell) {
                    return (
                      <td key={segment}>
                        <div className="flex h-12 items-center justify-center rounded bg-level-0 font-number-data text-number-data text-outline">
                          —
                        </div>
                      </td>
                    );
                  }
                  return (
                    <td key={segment}>
                      <button
                        type="button"
                        title={cell.caveat ?? formatShareOfVoice(cell.share_of_voice)}
                        onClick={() => onSelect?.(cell)}
                        className={cn(
                          "flex h-12 w-full flex-col items-center justify-center rounded font-number-data text-number-data tnum",
                          heatClass(cell.mention_count, peak),
                          cell.small_n && "border border-dashed border-error",
                        )}
                      >
                        {formatInteger(cell.mention_count)}
                      </button>
                    </td>
                  );
                })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
