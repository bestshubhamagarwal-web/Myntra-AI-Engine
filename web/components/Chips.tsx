import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { formatConfidence } from "@/lib/format";

export function ConfidenceChip({
  band,
  value,
}: {
  band: string;
  value?: number | null;
}) {
  const label = value != null ? `${formatConfidence(value)} · ${band}` : band;
  if (band === "answer") {
    return (
      <span className="inline-flex items-center rounded-sm border border-[#1b6b3a]/30 bg-[#e8f5ee] px-2 py-0.5 font-label-md text-[11px] uppercase tracking-wider text-[#1b6b3a]">
        {label}
      </span>
    );
  }
  if (band === "caveat") {
    return (
      <span className="inline-flex items-center rounded-sm border border-confidence-caveat/40 bg-[#fff7ed] px-2 py-0.5 font-label-md text-[11px] uppercase tracking-wider text-confidence-caveat">
        {label}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-sm border border-dashed border-error bg-transparent px-2 py-0.5 font-label-md text-[11px] uppercase tracking-wider text-error">
      {label}
    </span>
  );
}

export function IntentChip({ mode }: { mode: string | null | undefined }) {
  const value = mode || "unclear";
  if (value === "bookmark") {
    return (
      <span className="inline-flex items-center rounded-sm bg-tertiary-fixed px-2 py-0.5 font-label-md text-[11px] uppercase tracking-wider text-on-tertiary-fixed">
        bookmark
      </span>
    );
  }
  if (value === "stall") {
    return (
      <span className="inline-flex items-center rounded-sm bg-[#f4d6c8] px-2 py-0.5 font-label-md text-[11px] uppercase tracking-wider text-intent-stall">
        stall
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-sm border border-hairline bg-surface-variant px-2 py-0.5 font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">
      {value}
    </span>
  );
}

export function HypothesisBadge() {
  return (
    <span className="inline-flex items-center rounded-b-md border border-x border-b border-outline bg-surface-container-highest px-3 py-1 font-label-md text-[10px] font-medium uppercase tracking-wider text-on-surface-variant">
      Hypothesis
    </span>
  );
}

export function UnavailableBadge({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-hairline bg-surface-variant px-2.5 py-1 font-label-md text-label-md text-on-surface-variant">
      {label}: unavailable
    </span>
  );
}

export function LiveBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-outline-variant bg-secondary-container px-2.5 py-1 font-label-md text-label-md font-medium text-on-secondary-container">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
      Live
    </span>
  );
}

export function Chip({
  children,
  className,
  onClick,
  active,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  active?: boolean;
}) {
  const Comp = onClick ? "button" : "span";
  return (
    <Comp
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={cn(
        "inline-flex items-center rounded-sm px-2.5 py-1 font-label-md text-[11px]",
        active
          ? "bg-secondary-container text-on-secondary-container"
          : "border border-hairline bg-surface text-on-surface",
        onClick && "hover:bg-surface-container-low",
        className,
      )}
    >
      {children}
    </Comp>
  );
}
