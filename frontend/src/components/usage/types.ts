/** GET /api/usage/summary, /api/usage/calls and GET /api/ready (backend/routers/usage.py, services/usage_ledger.py). Every number is
 *  an estimate from the rates in `rates`: until the operator measures them they are defaults nobody has verified. */
export type UsageKind = "stt" | "stt_stream" | "tts" | "clone";

export interface KindUsage {
  requests: number;
  ok: number;
  credits: number;
  seconds: number;
  chars: number;
  cache_hits: number;
  cache_hit_rate: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  queue_p50_ms: number | null;
  queue_p95_ms: number | null;
  degraded: number;
}

export interface DegradedItem {
  ts: number | null;
  kind: UsageKind | null;
  lane: string | null;
  /** busy, breaker_open, server_error, rate_limited, timeout, unreachable, out_of_credits, auth_rejected, budget_reserve ... */
  reason: string | null;
  /** What the caller was told to do: hold_line, callback, queued, held_back. */
  action: string | null;
  message: string | null;
  conversation_id: string | null;
}

/** Always Gnani: Prisma hears, Timbre speaks. `state` says whether it can right now, and `why` in a sentence. */
export type ProviderState = "ready" | "retrying" | "unavailable" | "held_back" | "needs_key";

export interface ProviderInUse {
  provider: "gnani";
  name: "Gnani Prisma" | "Gnani Timbre";
  state: ProviderState;
  why: string;
}

export interface BudgetStatus {
  budget: number;
  spent: number;
  left: number;
  reserve_pct: number;
  reserve: number;
  usable_for_desk: number;
  left_for_desk: number;
  reserve_reached: boolean;
  exhausted: boolean;
  rupees_per_credit: number | null;
  spent_rupees: number | null;
  left_rupees: number | null;
}

export interface UsageSummary {
  window_days: number;
  estimate_note: string;
  rates_verified: boolean;
  rates: Record<string, { value: number; source: "default" | "env"; env: string }>;
  totals: { requests: number; credits: number; rupees: number | null; cache_hits: number; cache_hit_rate: number | null; degraded: number };
  by_kind: Partial<Record<UsageKind, KindUsage>>;
  queue_wait: { p50_ms: number | null; p95_ms: number | null };
  by_lane: Record<string, { requests: number; credits: number }>;
  /** Times Gnani could not answer: live callers were held or offered a callback, other work was queued. */
  degraded: { count: number; by_reason: Record<string, number>; by_action: Record<string, number>; hold_lines_played: number; recent: DegradedItem[] };
  calls: number;
  cost_per_call: number | null;
  cost_per_call_rupees: number | null;
  bookings: number | null;
  cost_per_booking: number | null;
  cost_per_booking_rupees: number | null;
  daily: { date: string; credits: number; requests: number }[];
  budget: BudgetStatus;
  providers: Record<"live" | "desk", Record<"stt" | "tts", ProviderInUse>>;
}

export interface CallUsage {
  credits: number;
  requests: number;
  cache_hits: number;
  degraded: number;
  degraded_reasons: string[];
  /** hold_line, callback, queued, held_back, hold_line_played, callback_offered */
  actions: string[];
}

export interface CallUsageMap {
  days: number;
  estimated: boolean;
  calls: Record<string, CallUsage>;
}
