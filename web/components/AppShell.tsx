"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { AuthGate } from "@/components/AuthGate";
import { DrawerSeedProvider, useDrawerSeed } from "@/components/DrawerSeed";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { FilterBar } from "@/components/FilterBar";
import { Sidebar } from "@/components/Sidebar";
import { cn } from "@/lib/cn";
import { ApiError } from "@/lib/types";
import { useEvidenceQuery, useOverviewQuery, useThemesQuery } from "@/lib/hooks";
import { FiltersProvider, useFilters } from "@/lib/filters";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <DrawerSeedProvider>
      <FiltersProvider>
        <AppShellInner>{children}</AppShellInner>
      </FiltersProvider>
    </DrawerSeedProvider>
  );
}

function AppShellInner({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const { filters, drawer, closeDrawer, searchParams } = useFilters();
  const { seed, setSeed } = useDrawerSeed();
  const overview = useOverviewQuery(filters);
  const themes = useThemesQuery(filters);
  const evidence = useEvidenceQuery(filters);

  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  const unauthorized =
    (overview.error as ApiError | undefined)?.status === 401 ||
    (themes.error as ApiError | undefined)?.status === 401;

  if (unauthorized) {
    return (
      <AuthGate
        onUnlocked={() => {
          void queryClient.invalidateQueries();
        }}
      />
    );
  }

  const evidenceSeed =
    evidence.data?.rows.find((row) => row.document_id === drawer.document_id) ?? seed;
  const flush = pathname === "/copilot";

  return (
    <>
      <div className="grid h-dvh max-w-[100vw] grid-rows-[auto_minmax(0,1fr)] overflow-hidden bg-level-0 md:grid-cols-[240px_minmax(0,1fr)]">
        <Sidebar
          overview={overview.data}
          themes={themes.data}
          search={searchParams.toString()}
          mobileOpen={navOpen}
          onClose={() => setNavOpen(false)}
        />
        <FilterBar
          overview={overview.data}
          themes={themes.data}
          onMenuClick={() => setNavOpen(true)}
        />
        <main
          className={cn(
            "min-h-0 min-w-0 overflow-x-hidden",
            flush ? "overflow-hidden p-0" : "overflow-y-auto p-4 md:p-6 xl:p-8",
          )}
        >
          {children}
        </main>
      </div>
      {navOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-inverse-surface/30 md:hidden"
          aria-label="Close navigation"
          onClick={() => setNavOpen(false)}
        />
      ) : null}
      <EvidenceDrawer
        open={Boolean(drawer.document_id)}
        documentId={drawer.document_id}
        chunkId={drawer.chunk_id}
        filters={filters}
        seed={evidenceSeed}
        onClose={() => {
          setSeed(null);
          closeDrawer();
        }}
      />
    </>
  );
}
