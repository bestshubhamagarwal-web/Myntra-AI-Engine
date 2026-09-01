"use client";

import { ThemeCard } from "@/components/ThemeCard";
import { EmptyState, ErrorState, PageSkeleton } from "@/components/States";
import { UnavailableBanner } from "@/components/UnavailableBanner";
import { useFilters } from "@/lib/filters";
import { useEvidenceQuery, useOverviewQuery, useThemesQuery } from "@/lib/hooks";

export default function ThemesPage() {
  const { filters, openDrawer, setFilters } = useFilters();
  const overview = useOverviewQuery(filters);
  const themes = useThemesQuery(filters);
  const evidence = useEvidenceQuery(filters);

  if (themes.isPending && !themes.data) return <PageSkeleton />;
  if (themes.error) {
    return <ErrorState message={themes.error.message} onRetry={() => themes.refetch()} />;
  }
  const data = themes.data;
  if (!data) return null;

  function openTheme(themeId: string) {
    setFilters({ theme_id: themeId });
    const row = evidence.data?.rows.find((item) => item.theme_id === themeId);
    if (row) openDrawer(row.document_id, row.chunk_id);
  }

  return (
    <div className="mx-auto max-w-5xl">
      <UnavailableBanner overview={overview.data} />
      <div className="mb-8 mt-4">
        <h2 className="mb-2 font-display text-display text-on-surface">Opportunity areas</h2>
        <p className="flex items-center gap-2 font-body-lg text-body-lg text-on-surface-variant">
          Ranked by impact from the Query API. Click a card to open quotes.
        </p>
        <p className="mt-1 font-label-md text-label-md text-on-surface-variant">
          Denominator: {data.denominator_definition}
          {data.themes_refreshed_at ? ` · themes refreshed ${data.themes_refreshed_at}` : ""}
        </p>
      </div>
      {data.empty || !data.themes.length ? (
        <EmptyState
          title="No opportunity areas in this slice"
          body="There are no published themes for the current filters. Empty is intentional — prior-week ranks are not reused."
        />
      ) : (
        <div className="space-y-4 pb-16">
          {data.themes.map((theme) => (
            <ThemeCard
              key={theme.theme_id}
              theme={theme}
              active={filters.theme_id === theme.theme_id}
              onOpen={() => openTheme(theme.theme_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
