"use client";

import { ConfidenceChip, HypothesisBadge, IntentChip } from "@/components/Chips";
import { Icon } from "@/components/Icon";
import { Sparkline } from "@/components/Sparkline";
import { cn } from "@/lib/cn";
import { labeledSources, operatorUnavailable } from "@/lib/sources";
import {
  formatConfidence,
  formatImpact,
  formatInteger,
  formatShareOfVoice,
  formatSentimentSkew,
  rankLabel,
} from "@/lib/format";
import type { ThemeCard as ThemeCardModel } from "@/lib/types";

export function ThemeCard({
  theme,
  active,
  onOpen,
}: {
  theme: ThemeCardModel;
  active?: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "relative flex w-full min-w-0 flex-col gap-4 overflow-hidden rounded-lg p-4 text-left transition-colors sm:flex-row sm:gap-6 sm:p-card",
        theme.hypothesis_flag
          ? "border-2 border-dashed border-outline bg-surface-container-low hover:bg-surface-variant"
          : "card-surface hover:bg-surface-container-low",
        active && "border-primary shadow-[0_0_0_1px_rgba(185,0,65,0.2)]",
      )}
    >
      {active ? <span className="absolute bottom-0 left-0 top-0 w-1 bg-primary" /> : null}
      {theme.hypothesis_flag ? (
        <span className="absolute right-8 top-0">
          <HypothesisBadge />
        </span>
      ) : null}
      <div className="flex shrink-0 items-center gap-3 border-outline-variant sm:w-12 sm:flex-col sm:justify-center sm:border-r sm:pr-6">
        <span className="mb-1 font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
          Rank
        </span>
        <span
          className={cn(
            "font-display text-display tnum",
            active ? "text-primary" : "text-on-surface-variant",
          )}
        >
          {rankLabel(theme.rank)}
        </span>
      </div>
      <div className="min-w-0 flex-1 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="font-headline-md text-headline-md text-on-surface">{theme.name}</h3>
            {theme.description ? (
              <p className="mt-1 max-w-2xl font-body-md text-body-md text-on-surface-variant">
                {theme.description}
              </p>
            ) : null}
          </div>
          <Icon name="chevron_right" className="shrink-0 text-primary" />
        </div>
        <div className="flex flex-wrap items-end justify-between gap-4 border-t border-outline-variant/50 pt-4">
          <div className="flex flex-wrap gap-2">
            <IntentChip mode={theme.bookmark_vs_stall} />
            <ConfidenceChip band={theme.confidence_band} value={theme.data_confidence} />
            {theme.trend_direction ? (
              <span className="inline-flex items-center gap-1 rounded-sm bg-surface-variant px-2 py-0.5 font-label-md text-[11px] uppercase tracking-wider text-on-surface-variant">
                <Icon
                  name={
                    theme.trend_direction === "rising"
                      ? "trending_up"
                      : theme.trend_direction === "declining"
                        ? "trending_down"
                        : "trending_flat"
                  }
                  className="text-[14px]"
                />
                {theme.trend_direction}
              </span>
            ) : null}
            {operatorUnavailable(theme.unavailable_sources).length ? (
              <span className="inline-flex items-center rounded-sm border border-dashed border-outline px-2 py-0.5 font-label-md text-[11px] text-on-surface-variant">
                store ingest issue: {labeledSources(operatorUnavailable(theme.unavailable_sources))}
              </span>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-3 text-left sm:text-right">
            <Metric label="Impact" value={formatImpact(theme.impact_score)} />
            <Metric label="SoV" value={formatShareOfVoice(theme.share_of_voice)} />
            <Metric label="Mentions" value={formatInteger(theme.mention_count)} />
            <Metric
              label="Sentiment"
              value={formatSentimentSkew(theme.sentiment_skew)}
              tone={
                theme.sentiment_skew != null && theme.sentiment_skew < 0 ? "error" : undefined
              }
            />
            <Metric label="Confidence" value={formatConfidence(theme.data_confidence)} />
            <Sparkline points={theme.sparkline} insufficient={theme.sparkline_insufficient} />
          </div>
        </div>
        <p className="font-label-md text-[11px] text-on-surface-variant">
          Denominator: {theme.denominator_definition} · n eligible {formatInteger(theme.eligible_corpus_count)}
        </p>
      </div>
    </button>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "error";
}) {
  return (
    <div>
      <div className="mb-1 font-label-md text-label-md text-on-surface-variant">{label}</div>
      <div
        className={cn(
          "font-number-data text-lg font-medium tnum text-on-surface",
          tone === "error" && "text-error",
        )}
      >
        {value}
      </div>
    </div>
  );
}
