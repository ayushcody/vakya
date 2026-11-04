"use client";

import { useMemo } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import type { CallerProfile } from "@/components/memory/types";

export type { CallerProfile } from "@/components/memory/types";

export interface CallersResponse {
  enabled: boolean;
  callers: CallerProfile[];
  total: number;
  limit: number;
  offset: number;
}

export const CALLERS_KEY = "/api/callers?limit=200";

export interface KnownCaller {
  profile: CallerProfile;
  /** The caller was already known when this call started (shows the "Returning caller" badge). */
  returning: boolean;
}

/**
 * Which conversations belong to a remembered caller, for the Calls page (one request shared by every row). A missing,
 * switched-off or failed answer is simply no badge and no card.
 */
export function useKnownCallers(): { byCall: Record<string, KnownCaller>; refresh: () => Promise<unknown> } {
  const { data, mutate } = useSWR<CallersResponse>(CALLERS_KEY, (p: string) => api.get<CallersResponse>(p), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
  const byCall = useMemo(() => {
    const out: Record<string, KnownCaller> = {};
    const list = Array.isArray(data?.callers) ? data.callers : [];
    for (const profile of list) {
      for (const c of Array.isArray(profile.calls) ? profile.calls : []) {
        out[c.conversation_id] = { profile, returning: !!c.returning };
      }
    }
    return out;
  }, [data]);
  return { byCall, refresh: () => mutate() };
}
