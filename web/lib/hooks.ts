"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  fetchEvidence,
  fetchNgrams,
  fetchOverview,
  fetchSegments,
  fetchThemes,
  fetchTrends,
} from "./api";
import { apiQueryFromFilters, type FilterState } from "./filters";

function filterKey(filters: FilterState): string {
  return apiQueryFromFilters(filters);
}

export function useOverviewQuery(filters: FilterState) {
  return useQuery({
    queryKey: ["overview", filterKey(filters)],
    queryFn: () => fetchOverview(filters),
    placeholderData: keepPreviousData,
  });
}

export function useThemesQuery(filters: FilterState) {
  return useQuery({
    queryKey: ["themes", filterKey(filters)],
    queryFn: () => fetchThemes(filters),
    placeholderData: keepPreviousData,
  });
}

export function useSegmentsQuery(filters: FilterState, dimension: string) {
  return useQuery({
    queryKey: ["segments", filterKey(filters), dimension],
    queryFn: () => fetchSegments(filters, dimension),
    placeholderData: keepPreviousData,
  });
}

export function useTrendsQuery(filters: FilterState) {
  return useQuery({
    queryKey: ["trends", filterKey(filters)],
    queryFn: () => fetchTrends(filters),
    placeholderData: keepPreviousData,
  });
}

export function useNgramsQuery(filters: FilterState) {
  return useQuery({
    queryKey: ["ngrams", filterKey(filters)],
    queryFn: () => fetchNgrams(filters),
    placeholderData: keepPreviousData,
  });
}

export function useEvidenceQuery(filters: FilterState) {
  return useQuery({
    queryKey: ["evidence", filterKey(filters)],
    queryFn: () => fetchEvidence(filters),
    placeholderData: keepPreviousData,
  });
}
