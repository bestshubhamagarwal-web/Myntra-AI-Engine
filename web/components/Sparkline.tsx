"use client";

import type { SparkPoint } from "@/lib/types";

export function Sparkline({
  points,
  insufficient,
}: {
  points: SparkPoint[];
  insufficient: boolean;
}) {
  if (!points.length || insufficient) {
    const y = points[0]?.mention_count;
    return (
      <div className="flex h-8 max-w-[6rem] flex-col items-end justify-end">
        {points.length === 1 ? (
          <svg className="h-8 w-24 max-w-full" viewBox="0 0 100 30" aria-hidden>
            <circle cx="50" cy="15" r="3" fill="currentColor" className="text-outline" />
            {y != null ? null : null}
          </svg>
        ) : (
          <span className="text-right font-label-md text-[10px] leading-tight text-on-surface-variant">
            insufficient
          </span>
        )}
      </div>
    );
  }

  const values = points.map((p) => p.mention_count);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const coords = values
    .map((v, i) => {
      const x = values.length === 1 ? 50 : (i / (values.length - 1)) * 100;
      const y = 28 - ((v - min) / span) * 24;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg className="h-8 w-24 max-w-full stroke-primary fill-none" viewBox="0 0 100 30" aria-hidden>
      <polyline points={coords} strokeWidth="2" />
    </svg>
  );
}
