"use client";

import { useMemo } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/formatDate";
import type { CallDetail, CallSummary, CallsResponse } from "@/app/(app)/desk/calls/types";

const quiet = { revalidateOnFocus: true, shouldRetryOnError: false, refreshInterval: 30000 } as const;

/** The conversations of the last `days` days, newest first (GET /api/calls). */
export function useCalls(days = 30, limit = 100, agentId?: string) {
  const key = `/api/calls?days=${days}&limit=${limit}${agentId ? `&agent_id=${encodeURIComponent(agentId)}` : ""}`;
  return useSWR<CallsResponse>(key, (p: string) => api.get<CallsResponse>(p), quiet);
}

/** True when an ISO time falls on the same calendar day (in this browser's clock) as `now`. */
export function isSameDay(iso: string | null | undefined, now: Date = new Date()): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  return !Number.isNaN(d.getTime()) && d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

/** One row of GET /api/bookings (backend/routers/bookings.py): a real appointment from the appointments store. */
export interface ApiBooking {
  id: string;
  name: string | null;
  /** Masked by the server: only the last four digits ("••••3210"). */
  phone: string | null;
  /** The doctor or partner. */
  service: string | null;
  specialty: string | null;
  reason: string | null;
  /** ISO 8601 with the offset of `timezone`. */
  start: string | null;
  timezone: string;
  status: "booked" | "cancelled";
  conversation_id: string | null;
  receipt_id: string | null;
  agent_id: string | null;
  created_at: string | null;
  calendar_sync: "synced" | "pending" | "not_configured" | "failed";
  /** The schedule it is on (schedules, 3 Nov); older rows have none. */
  schedule_id?: string | null;
  schedule_name?: string | null;
  /** When the booking ends (ISO); for a nightly stay, the check-out. */
  end?: string | null;
  nights?: number | null;
  /** Who it is for (backend/routers/bookings.py): the last four digits only, where it came from, an opaque caller id. */
  phone_masked?: string | null;
  source?: "phone" | "missed_call" | "web" | "staff";
  caller_ref?: string | null;
  caller_bookings_count?: number | null;
  language?: string | null;
  /** The proof of the booking (the caller's words, the read-back and the yes) was recorded. */
  has_evidence?: boolean;
  evidence_id?: string | null;
  /** Today's and later days' place in the queue (backend/services/schedule_bookings.token_map), when the server sends it. */
  token?: number | null;
  /** Who made the booking for this person and how they are related ("Rahul", "son"), when the stored row says. */
  booked_by?: string | null;
  relation?: string | null;
}

export interface BookingsResponse {
  bookings: ApiBooking[];
  timezone: string;
}

/** An appointment, from the bookings store or (only when that endpoint fails) worked out from the calls. */
export interface Booking {
  /** The booking id ("A12"); null when it was worked out from a call transcript. */
  id: string | null;
  /** The conversation it was booked on (open it on the Calls page). */
  callId: string | null;
  name: string | null;
  phone: string | null;
  service: string | null;
  reason: string | null;
  /** When the appointment starts; null in the calls-derived fallback, which does not know. */
  start: string | null;
  timezone: string | null;
  /** When it was booked. */
  createdAt: string | null;
  status: "booked" | "cancelled";
  calendar: ApiBooking["calendar_sync"] | null;
  agentName: string | null;
  agentId?: string | null;
  scheduleId?: string | null;
  scheduleName?: string | null;
  end?: string | null;
  nights?: number | null;
  phoneMasked?: string | null;
  source?: "phone" | "missed_call" | "web" | "staff" | null;
  callerRef?: string | null;
  callerBookingsCount?: number | null;
  language?: string | null;
  receiptId?: string | null;
  hasEvidence?: boolean;
  /** Token of the day for a time-slot booking of today or later (1-based by start time); see `withTokens`. */
  token?: number | null;
  bookedBy?: string | null;
  relation?: string | null;
  practice: boolean;
  /** What the booking tool said (calls-derived fallback only), e.g. "booking A12 with Dr. Meera Kulkarni on Monday 2026-10-19 10:30". */
  lines: string[];
  fromCalls: boolean;
}

/** True when an ISO time falls on the same calendar day as `now` in the given time zone. */
export function isSameDayIn(iso: string | null | undefined, timeZone: string | null | undefined, now: Date = new Date()): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  try {
    const day = (x: Date) => x.toLocaleDateString("en-CA", { timeZone: timeZone || undefined });
    return day(d) === day(now);
  } catch {
    return isSameDay(iso, now);
  }
}

/** "Mon 9 Nov, 10:30 am" in the practice's own time zone (lib/formatDate.ts). */
export function appointmentLabel(b: Pick<Booking, "start" | "timezone">): string {
  return formatDateTime(b.start, b.timezone) || "Time not recorded";
}

/** True when the appointment is booked (not cancelled) and starts on this day; the calls-derived fallback counts the day it was booked. */
export function isToday(b: Booking, now: Date = new Date()): boolean {
  return b.status === "booked" && (b.fromCalls ? isSameDay(b.createdAt, now) : isSameDayIn(b.start, b.timezone, now));
}

/** True when the booking was made today (on a call). */
export function madeToday(b: Booking, now: Date = new Date()): boolean {
  return b.status === "booked" && isSameDay(b.createdAt, now);
}

/** One stored appointment as the pages show it. */
export function fromApiBooking(b: ApiBooking, agentName: string | null = null): Booking {
  return {
    id: b.id,
    callId: b.conversation_id,
    name: b.name,
    phone: b.phone,
    service: b.service,
    reason: b.reason,
    start: b.start,
    timezone: b.timezone,
    createdAt: b.created_at,
    status: b.status,
    calendar: b.calendar_sync,
    agentName,
    agentId: b.agent_id,
    scheduleId: b.schedule_id ?? null,
    scheduleName: b.schedule_name ?? null,
    end: b.end ?? null,
    nights: b.nights ?? null,
    phoneMasked: b.phone_masked ?? b.phone,
    source: b.source ?? null,
    callerRef: b.caller_ref ?? null,
    callerBookingsCount: b.caller_bookings_count ?? null,
    language: b.language ?? null,
    receiptId: b.receipt_id,
    hasEvidence: !!b.has_evidence,
    token: b.token ?? null,
    bookedBy: b.booked_by ?? null,
    relation: b.relation ?? null,
    practice: false,
    lines: [],
    fromCalls: false,
  };
}

const idNumber = (id: string | null) => Number(/\d+/.exec(id ?? "")?.[0] ?? 0);

/**
 * Gives each live time-slot booking of today or a later day its token: its 1-based place among that day's live bookings of the
 * same schedule, ordered by start (the rule of backend/services/schedule_bookings.token_map, so the desk and the phone agree).
 * A token the server already sent is kept. Stays and bookings without a schedule have none.
 */
export function withTokens(list: Booking[], now: Date = new Date()): Booking[] {
  const groups = new Map<string, Booking[]>();
  for (const b of list) {
    if (b.status !== "booked" || b.fromCalls || !b.scheduleId || !b.start || b.nights || !b.id) continue;
    const day = b.start.slice(0, 10);
    if (day < now.toLocaleDateString("en-CA", { timeZone: b.timezone || undefined })) continue;
    const key = `${b.scheduleId}|${day}`;
    groups.set(key, [...(groups.get(key) ?? []), b]);
  }
  const tokens = new Map<string, number>();
  for (const rows of groups.values()) {
    rows
      .sort((a, b) => (a.start! < b.start! ? -1 : a.start! > b.start! ? 1 : idNumber(a.id) - idNumber(b.id)))
      .forEach((b, i) => tokens.set(b.id!, i + 1));
  }
  return list.map((b) => (b.id && tokens.has(b.id) ? { ...b, token: b.token ?? tokens.get(b.id)! } : b));
}

/** The upcoming bookings of one schedule, or of every schedule when `scheduleId` is empty (GET /api/bookings). */
export function useScheduleBookings(scheduleId: string | null | undefined, days = 30) {
  const key = `${bookingsKey(days)}${scheduleId ? `&schedule_id=${encodeURIComponent(scheduleId)}` : ""}`;
  const res = useSWR<BookingsResponse>(key, (p: string) => api.get<BookingsResponse>(p), quiet);
  const data = useMemo<Booking[]>(() => withTokens((res.data?.bookings ?? []).map((b) => fromApiBooking(b))), [res.data]);
  return { data, isLoading: res.isLoading, error: res.error, mutate: res.mutate };
}

const MAX_DETAIL = 40;
const DAY_MS = 86_400_000;
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** The key of the bookings request: appointments from `days` ago to 90 days ahead (the agent books at most 60 days ahead). */
export function bookingsKey(days: number, now: Date = new Date()): string {
  return `/api/bookings?from=${isoDay(new Date(now.getTime() - days * DAY_MS))}&to=${isoDay(new Date(now.getTime() + 90 * DAY_MS))}`;
}

/**
 * Bookings: the real appointments from GET /api/bookings. If that endpoint fails, they are worked out from the calls that
 * ended in a booking (each booked call's transcript is read once for the line the booking tool wrote), so the page still says
 * something. `calls` is the recent calls list, for the call counts.
 */
export function useBookings(days = 30) {
  const calls = useCalls(days, 100);
  const store = useSWR<BookingsResponse>(bookingsKey(days), (p: string) => api.get<BookingsResponse>(p), quiet);
  const fallback = !!store.error && !store.data;
  const booked = useMemo<CallSummary[]>(
    () => (fallback ? (calls.data?.calls ?? []).filter((c) => c.outcome.key === "booked").slice(0, MAX_DETAIL) : []),
    [fallback, calls.data]
  );
  const ids = booked.map((c) => c.id).join(",");
  const details = useSWR<Record<string, string[]>>(
    ids ? ["booking-lines", ids] : null,
    async () => {
      const out: Record<string, string[]> = {};
      await Promise.all(
        booked.map(async (c) => {
          try {
            const d = await api.get<CallDetail>(`/api/calls/${encodeURIComponent(c.id)}`);
            out[c.id] = d.transcript
              .flatMap((t) => t.actions)
              .filter((a) => a.ok && /book/i.test(a.name) && !/cancel/i.test(a.name))
              .map((a) => a.summary)
              .filter(Boolean);
          } catch {
            out[c.id] = [];
          }
        })
      );
      return out;
    },
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );
  const data = useMemo<Booking[]>(() => {
    if (store.data) {
      const agentNames = new Map((calls.data?.calls ?? []).map((c) => [c.id, c.agent_name]));
      return withTokens(store.data.bookings.map((b) => fromApiBooking(b, (b.conversation_id && agentNames.get(b.conversation_id)) || null)));
    }
    return booked.map((c) => ({
      id: null,
      callId: c.id,
      name: null,
      phone: c.caller,
      service: null,
      reason: null,
      start: null,
      timezone: null,
      createdAt: c.when,
      status: "booked" as const,
      calendar: null,
      agentName: c.agent_name,
      practice: c.practice,
      lines: details.data?.[c.id]?.length ? details.data[c.id] : c.outcome.detail ? [c.outcome.detail] : [],
      fromCalls: true,
    }));
  }, [store.data, calls.data, booked, details.data]);
  // The page is in error only when the bookings store and the calls both failed.
  const error = store.data ? undefined : store.error && calls.error ? calls.error : undefined;
  const isLoading = !store.data && !fallback ? store.isLoading : calls.isLoading;
  const mutate = async () => {
    await Promise.all([store.mutate(), calls.mutate()]);
  };
  return { data, isLoading, error, mutate, calls: calls.data?.calls ?? [], fromCalls: fallback };
}

/** Cancel one appointment (DELETE /api/bookings/{id}). */
export function cancelBooking(id: string) {
  return api.del<{ booking: ApiBooking }>(`/api/bookings/${encodeURIComponent(id)}`);
}
