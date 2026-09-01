"use client";

import { cn } from "@/lib/cn";
import type { NgramRow } from "@/lib/types";

export function PhraseCloud({ rows }: { rows: NgramRow[] }) {
  let peak = 0;
  for (const row of rows) {
    if (row.count > peak) peak = row.count;
  }

  return (
    <div className="flex min-h-[280px] flex-wrap items-center justify-center gap-x-4 gap-y-3 p-6">
      {rows.map((row) => {
        const ratio = peak > 0 ? row.count / peak : 0;
        const size = 12 + Math.round(ratio * 28);
        const sentiment = (row.sentiment || "").toLowerCase();
        return (
          <span
            key={`${row.n}:${row.gram}:${row.theme_id ?? ""}:${row.category ?? ""}`}
            title={`${row.gram} · ${row.count}`}
            className={cn(
              "leading-none",
              sentiment.includes("frustrat") || sentiment.includes("neg")
                ? "text-error"
                : sentiment.includes("delight") || sentiment.includes("pos")
                  ? "text-tertiary"
                  : "text-on-surface",
            )}
            style={{ fontSize: size, fontWeight: ratio > 0.6 ? 600 : 500 }}
          >
            {row.gram}
          </span>
        );
      })}
    </div>
  );
}
