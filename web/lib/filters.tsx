"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  createContext,
  startTransition,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  DRAWER_KEYS,
  FILTER_KEYS,
  type DrawerState,
  type FilterKey,
  type FilterState,
} from "./constants";

export type { FilterState, FilterKey, DrawerState };

function readParams(
  searchParams: URLSearchParams,
  keys: readonly string[],
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of keys) {
    const value = searchParams.get(key);
    if (value) out[key] = value;
  }
  return out;
}

export function filtersToSearchParams(filters: FilterState, extra?: URLSearchParams): string {
  const params = extra ? new URLSearchParams(extra.toString()) : new URLSearchParams();
  for (const key of FILTER_KEYS) {
    params.delete(key);
    const value = filters[key];
    if (value) params.set(key, value);
  }
  return params.toString();
}

export function apiQueryFromFilters(filters: FilterState): string {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value) params.set(key, value);
  }
  return params.toString();
}

export function withCurrentFilters(href: string, search: string): string {
  const [path, existing] = href.split("?");
  const next = new URLSearchParams(search);
  if (existing) {
    new URLSearchParams(existing).forEach((value, key) => next.set(key, value));
  }
  for (const key of DRAWER_KEYS) next.delete(key);
  const qs = next.toString();
  return qs ? `${path}?${qs}` : path;
}

type FiltersApi = {
  filters: FilterState;
  drawer: DrawerState;
  filterQuery: string;
  searchParams: URLSearchParams;
  setFilter: (key: FilterKey, value: string | null | undefined) => void;
  setFilters: (
    patch: Partial<Record<FilterKey | "document_id" | "chunk_id" | "dimension", string | null>>,
  ) => void;
  openDrawer: (documentId: string, chunkId?: string | null) => void;
  closeDrawer: () => void;
};

const FiltersContext = createContext<FiltersApi | null>(null);

function useFiltersStore(): FiltersApi {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const urlSearch = searchParams.toString();
  const [optimisticSearch, setOptimisticSearch] = useState<string | null>(null);

  useEffect(() => {
    setOptimisticSearch(null);
  }, [urlSearch]);

  const effectiveSearch = optimisticSearch ?? urlSearch;
  const params = useMemo(() => new URLSearchParams(effectiveSearch), [effectiveSearch]);

  const filters = useMemo(
    () => readParams(params, FILTER_KEYS) as FilterState,
    [params],
  );

  const drawer = useMemo(
    () => readParams(params, DRAWER_KEYS) as DrawerState,
    [params],
  );

  const replace = useCallback(
    (mutate: (next: URLSearchParams) => void) => {
      const next = new URLSearchParams(effectiveSearch);
      mutate(next);
      const qs = next.toString();
      setOptimisticSearch(qs);
      const href = qs ? `${pathname}?${qs}` : pathname;
      if (typeof window !== "undefined") {
        window.history.replaceState(window.history.state ?? {}, "", href);
      }
      startTransition(() => {
        router.replace(href, { scroll: false });
      });
    },
    [effectiveSearch, pathname, router],
  );

  const setFilter = useCallback(
    (key: FilterKey, value: string | null | undefined) => {
      replace((next) => {
        if (!value) next.delete(key);
        else next.set(key, value);
      });
    },
    [replace],
  );

  const setFilters = useCallback(
    (patch: Partial<Record<FilterKey | "document_id" | "chunk_id" | "dimension", string | null>>) => {
      replace((next) => {
        for (const [key, value] of Object.entries(patch)) {
          if (!value) next.delete(key);
          else next.set(key, value);
        }
      });
    },
    [replace],
  );

  const openDrawer = useCallback(
    (documentId: string, chunkId?: string | null) => {
      replace((next) => {
        next.set("document_id", documentId);
        if (chunkId) next.set("chunk_id", chunkId);
        else next.delete("chunk_id");
      });
    },
    [replace],
  );

  const closeDrawer = useCallback(() => {
    replace((next) => {
      next.delete("document_id");
      next.delete("chunk_id");
    });
  }, [replace]);

  const filterQuery = useMemo(() => apiQueryFromFilters(filters), [filters]);

  return {
    filters,
    drawer,
    filterQuery,
    searchParams: params,
    setFilter,
    setFilters,
    openDrawer,
    closeDrawer,
  };
}

export function FiltersProvider({ children }: { children: ReactNode }) {
  const value = useFiltersStore();
  return <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>;
}

export function useFilters(): FiltersApi {
  const ctx = useContext(FiltersContext);
  if (!ctx) {
    throw new Error("useFilters must be used within FiltersProvider");
  }
  return ctx;
}

export function ninetyDaysAgoIso(): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - 90);
  return date.toISOString().slice(0, 10);
}

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}
