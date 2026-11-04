import type { KindUsage } from "./types";

/** Credits for a person: more digits for small amounts, none for big ones. */
export function formatCredits(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 100) return Math.round(n).toLocaleString("en-IN");
  if (abs >= 10) return n.toFixed(1);
  return n.toFixed(2);
}

export function formatRupees(n: number | null | undefined): string {
  return n == null || !Number.isFinite(n) ? "—" : `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** A duration in milliseconds as "610 ms" or "1.2 s". */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

export function formatPercent(rate: number | null | undefined): string {
  return rate == null || !Number.isFinite(rate) ? "—" : `${Math.round(rate * 100)}%`;
}

const PASSING = new Set(["busy", "rate_limited", "timeout", "breaker_probe"]);

/** What happened when Gnani could not answer, in the words the desk uses. There is no second voice or recogniser: a live
 *  caller is held and then offered a callback, and other work waits to be retried. */
export function degradedLabel(reason: string | null | undefined, action?: string | null): string {
  if (reason === "budget_reserve" || action === "held_back") return "Held back to keep the live-call reserve";
  if (action === "callback" || action === "callback_offered") return "Gnani unavailable, offered a callback";
  if (action === "queued" && reason && !PASSING.has(reason)) return "Gnani unavailable, queued to retry";
  return "Gnani busy, retrying";
}

export const KIND_LABEL: Record<string, string> = { stt: "Recognition", stt_stream: "Live recognition", tts: "Voice", clone: "Voice clone" };

/** Latency of one kind as "p50 610 ms · p95 1.2 s", or null when nothing was measured. */
export function latencyLine(k: KindUsage | undefined): string | null {
  if (!k || k.latency_p50_ms == null) return null;
  return `p50 ${formatMs(k.latency_p50_ms)} · p95 ${formatMs(k.latency_p95_ms)}`;
}
