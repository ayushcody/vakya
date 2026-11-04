"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { CallUsageMap, UsageSummary } from "./types";

const quiet = { revalidateOnFocus: false, shouldRetryOnError: false, refreshInterval: 30000 } as const;

/** GET /api/usage/summary: credits, latency, cache, times Gnani was busy or down, cost per booking, the budget and the state per lane. */
export function useUsageSummary(days = 7) {
  return useSWR<UsageSummary>(`/api/usage/summary?days=${days}`, (p: string) => api.get<UsageSummary>(p), quiet);
}

/** GET /api/usage/calls: what each recent call used, keyed by conversation id (one request for the whole calls list). */
export function useCallUsage(days = 30) {
  return useSWR<CallUsageMap>(`/api/usage/calls?days=${days}`, (p: string) => api.get<CallUsageMap>(p), quiet);
}
