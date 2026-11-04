"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";

/** One row of GET /api/phone/missed-calls (backend/services/missed_calls.py). A missed call has several rows, one per step, sharing `hit`. */
export interface MissedCallEvent {
  id: string;
  hit: string;
  /** ISO time, UTC. */
  at: string;
  status: "received" | "skipped" | "called_back" | "failed";
  reason: string | null;
  /** Masked by the server. */
  number_masked?: string;
  agent_name?: string;
  message?: string;
}

export interface MissedCallsResponse {
  enabled: boolean;
  delay_s: number;
  dedupe_s: number;
  events: MissedCallEvent[];
}

/** POST /api/phone/missed-call-link */
export interface MissedCallLink {
  url: string;
  method: string;
  valid_days: number;
  enabled: boolean;
  delay_s: number;
  dedupe_s: number;
  setup: string;
}

/** One line per missed call: the latest step of each `hit`, newest first. */
export interface MissedCallLine {
  hit: string;
  at: string;
  number: string;
  status: MissedCallEvent["status"];
  text: string;
}

const REASON_TEXT: Record<string, string> = {
  disabled: "not called back: callbacks are switched off",
  bad_number: "not called back: no valid number",
  not_allowed: "not called back: the number is not in PHONE_ALLOWED_NUMBERS",
  recent_callback: "not called back: already called back a few minutes ago",
  call_live: "not called back: a call with this number was live",
  rate_limited: "not called back: too many calls this hour",
  not_configured: "not called back: the phone line is not set up",
  no_agent: "not called back: no front desk found",
};

export function lineText(e: MissedCallEvent): string {
  if (e.status === "called_back") return "called back";
  if (e.status === "received") return "missed call received, callback pending";
  if (e.status === "failed") return `callback failed${e.message ? `: ${e.message}` : ""}`;
  return REASON_TEXT[e.reason ?? ""] ?? `not called back${e.reason ? `: ${e.reason}` : ""}`;
}

/** The last `limit` missed calls, each as its latest event (events arrive newest first). */
export function groupMissedCalls(events: MissedCallEvent[], limit = 10): MissedCallLine[] {
  const seen = new Set<string>();
  const out: MissedCallLine[] = [];
  for (const e of events) {
    if (seen.has(e.hit)) continue;
    seen.add(e.hit);
    out.push({ hit: e.hit, at: e.at, number: e.number_masked ?? "Unknown number", status: e.status, text: lineText(e) });
    if (out.length >= limit) break;
  }
  return out;
}

const quiet = { revalidateOnFocus: true, shouldRetryOnError: false, refreshInterval: 15000 } as const;

/** The missed-call events and whether callbacks are on. */
export function useMissedCalls() {
  return useSWR<MissedCallsResponse>("/api/phone/missed-calls?limit=100", (p: string) => api.get<MissedCallsResponse>(p), quiet);
}

/** The Answer URL for the Vobiz application of the missed-call number, for this receptionist (built from PUBLIC_BASE_URL). */
export function useMissedCallLink(agentId: string | null | undefined) {
  // The result is stored with the agent it was fetched for, so a change of agent shows "loading" without resetting state inside the effect.
  const [result, setResult] = useState<{ agentId: string; link: MissedCallLink | null; error: string | null } | null>(null);
  useEffect(() => {
    if (!agentId) return;
    let live = true;
    (async () => {
      try {
        const r = await api.post<MissedCallLink>("/api/phone/missed-call-link", { agent_id: agentId });
        if (live) setResult({ agentId, link: r, error: null });
      } catch (e) {
        if (live) setResult({ agentId, link: null, error: e instanceof Error && e.message ? e.message : "Could not make the link." });
      }
    })();
    return () => {
      live = false;
    };
  }, [agentId]);
  const current = result && result.agentId === agentId ? result : null;
  return { link: current?.link ?? null, error: current?.error ?? null };
}
