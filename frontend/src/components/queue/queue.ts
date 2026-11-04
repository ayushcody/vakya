"use client";

/**
 * Running late and the token queue (contract: docs/RUNNING_LATE.md): the types of GET /api/schedules/{id}/today, the hook
 * that reads it, the calls that change it, and the words the desk shows. Pure helpers are exported so they can be tested.
 */

import useSWR from "swr";
import { api, ApiError } from "@/lib/api";
import { timeWords } from "@/lib/schedules";

export type TokenStatus = "seen" | "now" | "waiting";

export interface QueueToken {
  booking_id: string;
  /** 1-based place among the day's live bookings, by start time. */
  token: number;
  name: string;
  phone_masked?: string | null;
  /** "phone" | "staff" | "walk_in" | ... as stored. */
  source?: string;
  /** "HH:MM" local to the schedule. */
  slot_start: string;
  slot_end: string;
  expected_at: string;
  status: TokenStatus;
  people_ahead: number;
  moved_minutes: number;
  can_call: boolean;
  booked_by?: string;
  relation?: string;
}

export interface QueueAffected extends QueueToken {
  suggested_time: string;
}

export interface QueueToday {
  schedule_id: string;
  schedule_name: string;
  date: string;
  slot_minutes: number;
  minutes_late: number;
  reason: string | null;
  set_at: string | null;
  now_serving: number | null;
  count: number;
  tokens: QueueToken[];
  affected: QueueAffected[];
  calling: { ready: boolean; missing: string[] };
}

const enc = encodeURIComponent;
export const todayKey = (id: string) => `/api/schedules/${enc(id)}/today`;

/** The queue of a time-slot schedule for today, refreshed every 15 seconds. `null` id reads nothing. */
export function useToday(id: string | null | undefined) {
  return useSWR<QueueToday, ApiError>(id ? todayKey(id) : null, (p: string) => api.get<QueueToday>(p), {
    revalidateOnFocus: true,
    shouldRetryOnError: false,
    refreshInterval: 15_000,
  });
}

export const setDelay = (id: string, minutes_late: number, reason: string) =>
  api.post<QueueToday>(`/api/schedules/${enc(id)}/delay`, { minutes_late, reason: reason.trim() || null });
export const setNowServing = (id: string, token: number | "next") => api.post<QueueToday>(`/api/schedules/${enc(id)}/now-serving`, { token });
export const addWalkIn = (id: string, name: string, phone: string) =>
  api.post<{ booking: { booking_id: string; token?: number; time: string }; today: QueueToday }>(`/api/schedules/${enc(id)}/walk-in`, {
    name,
    ...(phone.trim() ? { phone: phone.trim() } : {}),
  });
export const notifyPatient = (id: string, bookingId: string) =>
  api.post<{ status: string; to: string }>(`/api/schedules/${enc(id)}/notify/${enc(bookingId)}`, {});

export const DELAY_CHOICES = [0, 15, 30, 45, 60] as const;
export const MAX_LATE = 480;
export const REASON_MAX = 80;

/** "On time", "15 min late", "1 hour late", "1 h 20 min late". */
export function lateWords(minutes: number): string {
  if (minutes <= 0) return "On time";
  if (minutes < 60) return `${minutes} min late`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${m} min late` : `${h === 1 ? "1 hour" : `${h} hours`} late`;
}

/** "Running 40 min late" for the banner. */
export const runningWords = (minutes: number) => `Running ${lateWords(minutes)}`;

/** "6:40 pm" from "18:40" (the same words as the calendar). */
export const clock = (hhmm: string) => timeWords(hhmm);

/** The words for a token's state: "In with the doctor", "Seen", or when it is expected. */
export function statusWords(t: Pick<QueueToken, "status" | "slot_start" | "expected_at" | "moved_minutes">): string {
  if (t.status === "now") return "In with the doctor";
  if (t.status === "seen") return "Seen";
  return t.moved_minutes >= 5 ? `About ${clock(t.expected_at)}` : clock(t.slot_start);
}

/** "Sunita Kulkarni · booked by Rahul (son)" parts: who booked it for them, when the row says. */
export function bookedByWords(b: { booked_by?: string | null; bookedBy?: string | null; relation?: string | null }): string | null {
  const by = b.booked_by ?? b.bookedBy;
  if (!by) return b.relation ? `booked by the caller, for their ${b.relation}` : null;
  return `booked by ${by}${b.relation ? `, for their ${b.relation}` : ""}`;
}

/** A sentence for the person at the desk when "Call to let them know" fails: never a status code or JSON. */
export function callError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return "Could not reach the server. Check that it is running, then try again.";
    if (e.code === "CALLING_NOT_SET_UP" || e.code === "PHONE_NOT_CONFIGURED") return "Calling is not set up yet, so please tell this patient yourself.";
    if (e.code === "NUMBER_NOT_ALLOWED") return "That number is not on the list of numbers the desk may call. Tell the patient yourself.";
    if (e.code === "NO_PHONE") return "This booking has no phone number to call.";
    if (e.code === "NOTHING_TO_TELL") return "This patient's time has not moved enough to need a call.";
    if (e.code === "RATE_LIMITED") return "Too many calls this hour. Please try again later, or tell them yourself.";
    if (e.status === 401 || e.status === 403) return "You are not signed in, or not allowed to do that.";
    const m = e.message.trim();
    if (m && m.length <= 200 && !/[{}[\]]/.test(m)) return m;
  }
  return "Could not place the call. Tell the patient yourself.";
}

/** The expected clock time of each waiting booking that moved, by slot start ("18:00" -> "6:40 pm"), for the calendar. */
export function expectedBySlot(q: QueueToday | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const t of q?.tokens ?? []) if (t.status === "waiting" && t.moved_minutes >= 5) out[t.slot_start] = clock(t.expected_at);
  return out;
}
