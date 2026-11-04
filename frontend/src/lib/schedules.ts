"use client";

/**
 * Schedules: the model, the API client and the pure helpers behind the Bookings screens.
 *
 * Contract: docs/SCHEDULES.md. Everything that does not touch the network
 * is a pure function here (hours in plain words, day presets, draft validation, the calendar grid, drag selection) so it
 * can be tested without rendering.
 */

import useSWR, { type SWRConfiguration } from "swr";
import { api, ApiError } from "@/lib/api";
import { deskKeyOfAgent } from "@/components/illustrations/deskTypes";
import { formatDay, formatDayLong, formatDayRange, formatMonthYear } from "@/lib/formatDate";

/* ------------------------------------------------------------------ model */

export const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type DayKey = (typeof DAY_KEYS)[number];
export const DAY_LONG: Record<DayKey, string> = {
  mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday",
};
export const DAY_SHORT: Record<DayKey, string> = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };

export interface Range {
  start: string;
  end: string;
}
export type Week = Record<DayKey, Range[]>;
export type ScheduleKind = "slots" | "nights";

export interface Schedule {
  id: string;
  agent_id: string | null;
  name: string;
  description: string | null;
  kind: ScheduleKind;
  accent: string;
  timezone: string;
  slot_minutes: number;
  buffer_minutes: number;
  capacity: number;
  week: Week;
  check_in: string | null;
  check_out: string | null;
  min_notice_minutes: number;
  horizon_days: number;
  closed_dates: string[];
  created_at?: string;
  updated_at?: string;
}

export interface ScheduleSummary {
  today_free: number;
  today_booked: number;
  next_free: string | null;
  hours_text: string;
}
export type ScheduleWithSummary = Schedule & { summary?: ScheduleSummary };

/** What POST and PUT accept: a schedule without its id, time stamps and zone (the server owns those). */
export type ScheduleInput = Omit<Schedule, "id" | "created_at" | "updated_at" | "timezone" | "accent"> & { accent?: string };

export type SlotStatus = "free" | "full" | "booked" | "blocked" | "past";
export interface Slot {
  start: string;
  end: string;
  status: SlotStatus;
  remaining: number;
  /** Everyone booked in this slot (all of them, not only the first), with who they are for. */
  bookings: SlotBooking[];
  block_id: string | null;
}
export type BookingSource = "phone" | "missed_call" | "web" | "staff";
export interface SlotBooking {
  id: string;
  name: string | null;
  /** Only the last four digits ("••••3210"): a full number never reaches the browser. */
  phone_masked?: string | null;
  source?: BookingSource;
  conversation_id?: string | null;
  /** An opaque id of the caller (backend/services/caller_ref.py), for /desk/callers?caller= . */
  caller_ref?: string | null;
  caller_bookings_count?: number | null;
  language?: string | null;
  nights?: number | null;
  has_evidence?: boolean;
}
export interface SlotDay {
  date: string;
  open: boolean;
  slots: Slot[];
}
export interface SlotsResponse {
  timezone: string;
  kind: ScheduleKind;
  days: SlotDay[];
}

export interface CheckOption {
  start: string;
  end: string;
}
export type CheckReason = "closed" | "full" | "blocked" | "past" | "outside_hours" | "too_soon" | "too_far" | "not_a_start" | "unclear";
export interface CheckResult {
  available: boolean;
  requested: CheckOption | null;
  reason: CheckReason | null;
  before: CheckOption | null;
  after: CheckOption | null;
  spoken: { en: string; hi: string };
}

export interface ParsedHours {
  week: Week;
  slot_minutes: number | null;
  closed_dates: string[];
  notes: string[];
  confident: boolean;
}

/* ------------------------------------------------------------ API client */

const enc = encodeURIComponent;

export const schedulesKey = (agentId?: string | null) => `/api/schedules${agentId ? `?agent_id=${enc(agentId)}` : ""}`;
export const slotsKey = (id: string, from: string, to: string) => `/api/schedules/${enc(id)}/slots?from=${from}&to=${to}`;

const quiet: SWRConfiguration = { revalidateOnFocus: true, shouldRetryOnError: false, refreshInterval: 30_000 };

async function fetchSchedules(path: string): Promise<ScheduleWithSummary[]> {
  const body = await api.get<{ schedules?: ScheduleWithSummary[] } | ScheduleWithSummary[]>(path);
  return Array.isArray(body) ? body : (body?.schedules ?? []);
}

/** The schedules of one front desk, or of every front desk when `agentId` is empty. */
export function useSchedules(agentId?: string | null) {
  return useSWR<ScheduleWithSummary[], ApiError>(schedulesKey(agentId), fetchSchedules, quiet);
}

/** One schedule. The contract has no single-schedule read, so it comes from the list (the same cache the cards use). */
export function useSchedule(id: string | null | undefined) {
  const all = useSWR<ScheduleWithSummary[], ApiError>(id ? schedulesKey() : null, fetchSchedules, quiet);
  const schedule = id ? all.data?.find((s) => s.id === id) : undefined;
  return { ...all, schedule, notFound: !!id && !!all.data && !schedule };
}

/** The slot grid for `from` to `to` (YYYY-MM-DD, at most 42 days). */
export function useSlots(id: string | null | undefined, from: string, to: string) {
  return useSWR<SlotsResponse, ApiError>(
    id ? slotsKey(id, from, to) : null,
    (p: string) => api.get<SlotsResponse>(p),
    { revalidateOnFocus: true, shouldRetryOnError: false, refreshInterval: 20_000, keepPreviousData: true }
  );
}

export async function createSchedule(input: ScheduleInput): Promise<Schedule> {
  return (await api.post<{ schedule: Schedule }>("/api/schedules", input)).schedule;
}
export async function updateSchedule(id: string, input: ScheduleInput): Promise<Schedule> {
  return (await api.put<{ schedule: Schedule }>(`/api/schedules/${enc(id)}`, input)).schedule;
}
export async function deleteSchedule(id: string, force = false): Promise<void> {
  await api.del(`/api/schedules/${enc(id)}${force ? "?force=1" : ""}`);
}
export async function createBlock(id: string, start: string, end: string, reason?: string): Promise<{ id: string }> {
  return (await api.post<{ block: { id: string } }>(`/api/schedules/${enc(id)}/blocks`, { start, end, reason: reason ?? null })).block;
}
export async function deleteBlock(id: string, blockId: string): Promise<void> {
  await api.del(`/api/schedules/${enc(id)}/blocks/${enc(blockId)}`);
}
export function checkSchedule(id: string, body: { text?: string; date?: string; time?: string; nights?: number; party?: number }) {
  return api.post<CheckResult>(`/api/schedules/${enc(id)}/check`, body);
}
export function parseHours(text: string) {
  return api.post<ParsedHours>("/api/schedules/parse-hours", { text });
}
export function bookSlot(body: { schedule_id: string; start: string; name: string; phone?: string; note?: string; nights?: number }) {
  return api.post<{ booking: { id: string } }>("/api/bookings", body);
}

/** True for the 409 the server sends when a schedule still has bookings to come. */
export function hasBookingsError(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 409 || e.code === "HAS_BOOKINGS");
}

/** A sentence for the person at the desk: never a status code, a code word or JSON. */
export function plainError(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return "Could not reach the server. Check that it is running, then try again.";
    if (e.status === 401 || e.status === 403) return "You are not signed in, or not allowed to do that.";
    if (e.status === 404) return "That could not be found. It may have been removed.";
    if (e.status === 409) return "That clashes with something already booked.";
  }
  const m = e instanceof Error ? e.message.trim() : "";
  if (m && m.length <= 140 && !/[{}[\]]|^http_|^[A-Z_]{4,}$|^Request failed|^Not Found$|^Internal/i.test(m)) return m;
  return fallback;
}

/* --------------------------------------------------------- time of day */

/**
 * Read a time someone typed: "5pm", "5:30 pm", "17:30", "1730", "noon", "midnight". A bare number that could be morning or
 * evening is read so the time comes after `after` (minutes) when that is given: an end time of "5" after a 9 am start is 5 pm.
 * Returns "HH:MM", or null when it is not a time.
 */
export function parseTimeInput(text: string, opts: { after?: number } = {}): string | null {
  const t = text.trim().toLowerCase().replace(/\./g, ":").replace(/\s+/g, "");
  if (!t) return null;
  if (t === "noon" || t === "midday") return "12:00";
  if (t === "midnight") return opts.after !== undefined && opts.after > 0 ? "24:00" : "00:00";
  const m = /^(\d{1,2}):?(\d{2})?(am|pm|a|p)?$/.exec(t);
  if (!m) return null;
  const h = Number(m[1]);
  const mm = m[2] ? Number(m[2]) : 0;
  if (mm > 59 || h > 24 || (h === 24 && mm !== 0)) return null;
  const mer = m[3]?.[0];
  if (mer) {
    if (h < 1 || h > 12) return null;
    return fromMin(((h % 12) + (mer === "p" ? 12 : 0)) * 60 + mm);
  }
  if (h === 0 || h > 12) return fromMin(h * 60 + mm);
  const am = (h % 12) * 60 + mm;
  const pm = am + 720;
  if (opts.after !== undefined && opts.after >= 0) return fromMin(am > opts.after ? am : pm);
  if (h === 12) return fromMin(720 + mm);
  return fromMin(h >= 7 ? am : pm);
}

/** "10:30" -> 630 minutes. "24:00" is the end of the day (1440). Anything unreadable is NaN. */
export function toMin(hhmm: string): number {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return NaN;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  if (mm > 59 || h > 24 || (h === 24 && mm !== 0)) return NaN;
  return h * 60 + mm;
}
export function fromMin(min: number): string {
  const c = Math.max(0, Math.min(1440, Math.round(min)));
  return `${String(Math.floor(c / 60)).padStart(2, "0")}:${String(c % 60).padStart(2, "0")}`;
}

function parts(min: number): { h: number; m: number; mer: "am" | "pm" } {
  const h24 = Math.floor(min / 60) % 24;
  return { h: h24 % 12 === 0 ? 12 : h24 % 12, m: min % 60, mer: h24 < 12 ? "am" : "pm" };
}
const isSpecial = (min: number) => min === 720 || min === 0 || min === 1440;

/** 630 -> "10:30 am", 780 -> "1 pm", 720 -> "noon", 1440 -> "midnight". */
export function timeWords(hhmm: string): string {
  const min = toMin(hhmm);
  if (Number.isNaN(min)) return hhmm;
  if (min === 720) return "noon";
  if (min === 1440 || min === 0) return "midnight";
  const p = parts(min);
  return `${p.h}${p.m ? `:${String(p.m).padStart(2, "0")}` : ""} ${p.mer}`;
}

/** "10 am to 1 pm", "5 to 9 pm", "all day". */
export function rangeWords(r: Range): string {
  const s = toMin(r.start);
  const e = toMin(r.end);
  if (Number.isNaN(s) || Number.isNaN(e)) return "";
  if (s === 0 && e === 1440) return "all day";
  if (isSpecial(s) || isSpecial(e)) return `${timeWords(r.start)} to ${timeWords(r.end)}`;
  const a = parts(s);
  const b = parts(e);
  if (a.mer === b.mer) {
    const num = (p: { h: number; m: number }) => `${p.h}${p.m ? `:${String(p.m).padStart(2, "0")}` : ""}`;
    return `${num(a)} to ${num(b)} ${b.mer}`;
  }
  return `${timeWords(r.start)} to ${timeWords(r.end)}`;
}

function joinAnd(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

export function rangesWords(ranges: Range[]): string {
  return joinAnd(ranges.map(rangeWords).filter(Boolean));
}

/* --------------------------------------------------- days and the week */

export const emptyWeek = (): Week => ({ mon: [], tue: [], wed: [], thu: [], fri: [], sat: [], sun: [] });

export function weekFor(days: readonly DayKey[], ranges: Range[]): Week {
  const w = emptyWeek();
  for (const d of days) w[d] = ranges.map((r) => ({ ...r }));
  return w;
}

export const openDaysOf = (week: Week): DayKey[] => DAY_KEYS.filter((d) => week[d]?.length > 0);

const sig = (ranges: Range[]) => ranges.map((r) => `${r.start}-${r.end}`).join("|");

/** "Mon to Sat", "Sat and Sun", "Mon, Wed and Fri". `long` spells the days out. */
export function dayList(days: readonly DayKey[], style: "short" | "long" = "short"): string {
  const names = style === "long" ? DAY_LONG : DAY_SHORT;
  const idx = DAY_KEYS.map((d) => days.includes(d));
  const runs: DayKey[][] = [];
  let cur: DayKey[] = [];
  DAY_KEYS.forEach((d, i) => {
    if (idx[i]) cur.push(d);
    else if (cur.length) {
      runs.push(cur);
      cur = [];
    }
  });
  if (cur.length) runs.push(cur);
  const labels = runs.flatMap((run) =>
    run.length >= 3 ? [`${names[run[0]]} to ${names[run[run.length - 1]]}`] : run.map((d) => names[d])
  );
  return joinAnd(labels);
}

/** "Mon to Sat, 10 am to 1 pm and 5 to 9 pm". Days with the same hours are grouped; closed days are left out. */
export function hoursInWords(week: Week, style: "short" | "long" = "short"): string {
  const groups = new Map<string, { days: DayKey[]; ranges: Range[] }>();
  for (const d of DAY_KEYS) {
    const r = week[d] ?? [];
    if (!r.length) continue;
    const k = sig(r);
    const g = groups.get(k);
    if (g) g.days.push(d);
    else groups.set(k, { days: [d], ranges: r });
  }
  if (groups.size === 0) return "Closed every day";
  return [...groups.values()]
    .map((g) => {
      const label = g.days.length === 7 ? (style === "long" ? "Every day" : "Every day") : dayList(g.days, style);
      return `${label}, ${rangesWords(g.ranges)}`;
    })
    .join("; ");
}

export interface DayPreset {
  id: "all" | "weekdays" | "monsat" | "weekends" | "closed";
  label: string;
  days: DayKey[];
}
export const DAY_PRESETS: DayPreset[] = [
  { id: "all", label: "All days", days: [...DAY_KEYS] },
  { id: "weekdays", label: "Weekdays", days: ["mon", "tue", "wed", "thu", "fri"] },
  { id: "monsat", label: "Mon to Sat", days: ["mon", "tue", "wed", "thu", "fri", "sat"] },
  { id: "weekends", label: "Weekends", days: ["sat", "sun"] },
  { id: "closed", label: "Closed", days: [] },
];
export function presetMatches(open: readonly DayKey[], preset: DayPreset): boolean {
  return open.length === preset.days.length && preset.days.every((d) => open.includes(d));
}
export const activePreset = (open: readonly DayKey[]): DayPreset | undefined => DAY_PRESETS.find((p) => presetMatches(open, p));
export const toggleDay = (open: readonly DayKey[], day: DayKey): DayKey[] =>
  DAY_KEYS.filter((d) => (d === day ? !open.includes(d) : open.includes(d)));

export interface HoursPreset {
  id: string;
  label: string;
  ranges: Range[];
}
export const HOURS_PRESETS: HoursPreset[] = [
  { id: "9to5", label: "9 to 5", ranges: [{ start: "09:00", end: "17:00" }] },
  { id: "split", label: "10 to 1 and 5 to 9", ranges: [{ start: "10:00", end: "13:00" }, { start: "17:00", end: "21:00" }] },
  { id: "24h", label: "Open 24 hours", ranges: [{ start: "00:00", end: "24:00" }] },
];

/** Open days of a nightly schedule carry one whole-day range; the check-in and check-out times live beside it. */
export const NIGHT_RANGE: Range = { start: "00:00", end: "24:00" };

/* --------------------------------------------------------------- draft */

export const SLOT_CHOICES = [10, 15, 20, 30, 45, 60] as const;

export interface ScheduleDraft {
  name: string;
  description: string;
  agent_id: string | null;
  kind: ScheduleKind;
  accent: string | null;
  openDays: DayKey[];
  sameHours: boolean;
  shared: Range[];
  perDay: Week;
  slot_minutes: number;
  buffer_minutes: number;
  capacity: number;
  check_in: string;
  check_out: string;
  min_notice_minutes: number;
  horizon_days: number;
  closed_dates: string[];
}

export function defaultDraft(agentId: string | null = null): ScheduleDraft {
  const shared = [{ start: "10:00", end: "13:00" }, { start: "17:00", end: "20:00" }];
  return {
    name: "",
    description: "",
    agent_id: agentId,
    kind: "slots",
    accent: null,
    openDays: ["mon", "tue", "wed", "thu", "fri", "sat"],
    sameHours: true,
    shared,
    perDay: weekFor(DAY_KEYS, shared),
    slot_minutes: 15,
    buffer_minutes: 0,
    capacity: 1,
    check_in: "12:00",
    check_out: "11:00",
    min_notice_minutes: 60,
    horizon_days: 30,
    closed_dates: [],
  };
}

/** The week a draft describes. */
export function draftWeek(d: ScheduleDraft): Week {
  if (d.kind === "nights") return weekFor(d.openDays, [NIGHT_RANGE]);
  const byStart = (a: Range, b: Range) => toMin(a.start) - toMin(b.start);
  if (d.sameHours) return weekFor(d.openDays, [...d.shared].sort(byStart));
  const w = emptyWeek();
  for (const day of d.openDays) w[day] = (d.perDay[day] ?? []).map((r) => ({ ...r })).sort(byStart);
  return w;
}

/** Turn a stored schedule back into a draft for the editor. */
export function scheduleToDraft(s: Schedule): ScheduleDraft {
  const base = defaultDraft(s.agent_id);
  const open = openDaysOf(s.week);
  const first = open.length ? s.week[open[0]] : base.shared;
  const same = open.every((d) => sig(s.week[d]) === sig(first));
  return {
    ...base,
    name: s.name,
    description: s.description ?? "",
    kind: s.kind,
    accent: s.accent || null,
    openDays: open,
    sameHours: s.kind === "nights" ? true : same,
    shared: s.kind === "nights" ? base.shared : first.map((r) => ({ ...r })),
    perDay: { ...weekFor(DAY_KEYS, first), ...Object.fromEntries(open.map((d) => [d, s.week[d].map((r) => ({ ...r }))])) } as Week,
    slot_minutes: s.slot_minutes,
    buffer_minutes: s.buffer_minutes,
    capacity: s.capacity,
    check_in: s.check_in ?? base.check_in,
    check_out: s.check_out ?? base.check_out,
    min_notice_minutes: s.min_notice_minutes,
    horizon_days: s.horizon_days,
    closed_dates: [...s.closed_dates],
  };
}

export function draftToInput(d: ScheduleDraft): ScheduleInput {
  const nights = d.kind === "nights";
  return {
    agent_id: d.agent_id,
    name: d.name.trim(),
    description: d.description.trim() || null,
    kind: d.kind,
    ...(d.accent ? { accent: d.accent } : {}),
    slot_minutes: nights ? 60 : d.slot_minutes,
    buffer_minutes: nights ? 0 : d.buffer_minutes,
    capacity: d.capacity,
    week: draftWeek(d),
    check_in: nights ? d.check_in : null,
    check_out: nights ? d.check_out : null,
    min_notice_minutes: d.min_notice_minutes,
    horizon_days: d.horizon_days,
    closed_dates: [...d.closed_dates].sort(),
  };
}

/** A draft with the parsed hours dropped in. `changed` names what moved, for the highlight. */
export function applyParsedHours(d: ScheduleDraft, p: ParsedHours): { draft: ScheduleDraft; changed: ("days" | "hours" | "slots" | "holidays")[] } {
  const open = openDaysOf(p.week);
  const first = open.length ? p.week[open[0]] : d.shared;
  const same = open.every((day) => sig(p.week[day]) === sig(first));
  const changed: ("days" | "hours" | "slots" | "holidays")[] = [];
  if (open.join() !== d.openDays.join()) changed.push("days");
  if (!same || sig(first) !== sig(d.shared) || !d.sameHours) changed.push("hours");
  const slot = p.slot_minutes && p.slot_minutes >= 5 ? p.slot_minutes : d.slot_minutes;
  if (slot !== d.slot_minutes) changed.push("slots");
  const closed = p.closed_dates?.length ? [...new Set([...d.closed_dates, ...p.closed_dates])].sort() : d.closed_dates;
  if (closed.length !== d.closed_dates.length) changed.push("holidays");
  return {
    draft: {
      ...d,
      openDays: open,
      sameHours: same,
      shared: first.map((r) => ({ ...r })),
      perDay: { ...weekFor(DAY_KEYS, first), ...Object.fromEntries(open.map((day) => [day, p.week[day].map((r) => ({ ...r }))])) } as Week,
      slot_minutes: slot,
      closed_dates: closed,
    },
    changed,
  };
}

/* ---------------------------------------------------------- validation */

export interface Issue {
  /** "name", "hours", "hours:mon", "slots", "stay" or "days". */
  field: string;
  message: string;
  /** Index of the range the message is about, when it is about one. */
  index?: number;
}

function sessionName(r: Range, i: number, all: Range[]): string {
  if (all.length === 1) return "opening hours";
  const s = toMin(r.start);
  const label = s < 720 ? "morning" : s < 1020 ? "afternoon" : "evening";
  const same = all.filter((x) => {
    const xs = toMin(x.start);
    return (xs < 720 ? "morning" : xs < 1020 ? "afternoon" : "evening") === label;
  }).length;
  if (same > 1) return `${["first", "second", "third", "fourth"][i] ?? `#${i + 1}`} session`;
  return `${label} session`;
}

/** Plain-words problems with a list of opening ranges. `field` tags each message for the day it belongs to. */
export function validateRanges(ranges: Range[], field = "hours"): Issue[] {
  const out: Issue[] = [];
  ranges.forEach((r, i) => {
    const s = toMin(r.start);
    const e = toMin(r.end);
    const name = sessionName(r, i, ranges);
    if (Number.isNaN(s) || Number.isNaN(e)) out.push({ field, index: i, message: `Pick a start and an end time for the ${name}.` });
    else if (e < s) out.push({ field, index: i, message: `The ${name} ends before it starts.` });
    else if (e === s) out.push({ field, index: i, message: `The ${name} starts and ends at the same time.` });
  });
  const ok = ranges
    .map((r, i) => ({ s: toMin(r.start), e: toMin(r.end), i }))
    .filter((x) => !Number.isNaN(x.s) && !Number.isNaN(x.e) && x.e > x.s)
    .sort((a, b) => a.s - b.s);
  for (let k = 1; k < ok.length; k++) {
    if (ok[k].s < ok[k - 1].e) {
      out.push({ field, index: ok[k].i, message: `The ${sessionName(ranges[ok[k - 1].i], ok[k - 1].i, ranges)} and the ${sessionName(ranges[ok[k].i], ok[k].i, ranges)} overlap. Move one so they do not touch.` });
      break;
    }
  }
  return out;
}

export function validateDraft(d: ScheduleDraft): Issue[] {
  const out: Issue[] = [];
  if (!d.name.trim()) out.push({ field: "name", message: "Give this schedule a name, like \"Doctor consultations\" or \"Rooms\"." });
  if (d.kind === "slots") {
    if (d.sameHours) {
      if (d.openDays.length) {
        if (!d.shared.length) out.push({ field: "hours", message: "Add the hours you are open." });
        out.push(...validateRanges(d.shared));
      }
    } else {
      for (const day of d.openDays) {
        const r = d.perDay[day] ?? [];
        if (!r.length) out.push({ field: `hours:${day}`, message: `Add opening hours for ${DAY_LONG[day]}, or close it.` });
        out.push(...validateRanges(r, `hours:${day}`).map((i) => ({ ...i, message: `${DAY_LONG[day]}: ${i.message}` })));
      }
    }
    const range = draftWeek(d);
    const shortest = Math.min(
      ...DAY_KEYS.flatMap((day) => range[day].map((r) => toMin(r.end) - toMin(r.start))).filter((n) => n > 0),
      Infinity
    );
    if (Number.isFinite(shortest) && shortest < d.slot_minutes) {
      out.push({ field: "slots", message: `One of your sessions is shorter than a ${d.slot_minutes}-minute slot, so nothing can be booked in it.` });
    }
    if (!Number.isInteger(d.slot_minutes) || d.slot_minutes < 5 || d.slot_minutes > 240) {
      out.push({ field: "slots", message: "A slot can be between 5 minutes and 4 hours long." });
    }
  } else {
    if (Number.isNaN(toMin(d.check_in)) || Number.isNaN(toMin(d.check_out))) {
      out.push({ field: "stay", message: "Pick a check-in and a check-out time." });
    } else if (toMin(d.check_out) > toMin(d.check_in)) {
      out.push({ field: "stay", message: "Check-out has to be earlier in the day than check-in, so one guest leaves before the next arrives." });
    }
  }
  if (!Number.isInteger(d.capacity) || d.capacity < 1 || d.capacity > 500) {
    out.push({ field: "slots", message: d.kind === "nights" ? "Rooms can be from 1 to 500." : "At the same time can be from 1 to 500." });
  }
  return out;
}

export const issuesFor = (issues: Issue[], field: string) => issues.filter((i) => i.field === field);

/* ------------------------------------------------------------- phrases */

function minutesWords(n: number): string {
  if (n < 60) return `${n}-minute`;
  if (n % 60 === 0) return n === 60 ? "1-hour" : `${n / 60}-hour`;
  return `${n}-minute`;
}
export function durationWords(n: number): string {
  if (n < 60) return `${n} minute${n === 1 ? "" : "s"}`;
  if (n % 1440 === 0) return `${n / 1440} day${n === 1440 ? "" : "s"}`;
  if (n % 60 === 0) return `${n / 60} hour${n === 60 ? "" : "s"}`;
  return `${n} minutes`;
}

/** "Every 15 min" or "Nightly, 10 rooms". */
export function kindLine(s: Pick<Schedule, "kind" | "slot_minutes" | "capacity">): string {
  if (s.kind === "nights") return `Nightly, ${s.capacity} room${s.capacity === 1 ? "" : "s"}`;
  return `Every ${s.slot_minutes} min${s.capacity > 1 ? `, ${s.capacity} at a time` : ""}`;
}

/** The sentence callers effectively hear: "Open Monday to Saturday, 10 am to 1 pm and 5 to 9 pm. 15-minute slots, one at a time." */
export function scheduleSentence(d: ScheduleDraft): string {
  const week = draftWeek(d);
  const open = openDaysOf(week);
  if (!open.length) return "Closed every day. Callers will be told there is nothing to book.";
  if (d.kind === "nights") {
    const rooms = `${d.capacity} room${d.capacity === 1 ? "" : "s"}`;
    const days = open.length === 7 ? "every night" : `on ${dayList(open, "long")}`;
    return `Nightly stays, ${rooms}, bookable ${days}. Check-in from ${timeWords(d.check_in)}, check-out by ${timeWords(d.check_out)}.`;
  }
  const hours = hoursInWords(week, "long");
  const at = d.capacity === 1 ? "one at a time" : `up to ${d.capacity} at the same time`;
  const gap = d.buffer_minutes > 0 ? `, with ${d.buffer_minutes} minutes between bookings` : "";
  return `Open ${hours.replace(/^Every day/, "every day")}. ${minutesWords(d.slot_minutes)} slots, ${at}${gap}.`;
}

/* --------------------------------------------------------------- dates */
// Dates are "YYYY-MM-DD" strings; all arithmetic is done in UTC so a daylight-saving change never moves a day.

const utc = (day: string) => {
  const [y, m, d] = day.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
};
const fromUtc = (ms: number) => new Date(ms).toISOString().slice(0, 10);

export const isoDayLocal = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const addDays = (day: string, n: number) => fromUtc(utc(day) + n * 86_400_000);
/** 0 = Monday ... 6 = Sunday. */
export const weekdayIndex = (day: string) => (new Date(utc(day)).getUTCDay() + 6) % 7;
export const dayKeyOf = (day: string): DayKey => DAY_KEYS[weekdayIndex(day)];
export const weekStart = (day: string) => addDays(day, -weekdayIndex(day));
export const weekDays = (day: string) => Array.from({ length: 7 }, (_, i) => addDays(weekStart(day), i));
export const monthStart = (day: string) => `${day.slice(0, 7)}-01`;
export const addMonths = (day: string, n: number) => {
  const [y, m] = day.split("-").map(Number);
  return fromUtc(Date.UTC(y, m - 1 + n, 1));
};
/** The 42 days of a month grid: it starts on the Monday on or before the 1st. */
export const monthGrid = (day: string) => Array.from({ length: 42 }, (_, i) => addDays(weekStart(monthStart(day)), i));

/** The labels all come from lib/formatDate.ts: "Thu 12 Nov", "Thursday 12 November", "November 2026", "9 to 15 Nov 2026". */
export const dayLabel = (day: string) => formatDay(day) ?? day;
export const dayLabelLong = (day: string) => formatDayLong(day);
export const monthLabel = (day: string) => formatMonthYear(day);
export function weekRangeLabel(day: string): string {
  const a = weekStart(day);
  return formatDayRange(a, addDays(a, 6));
}

/** The wall-clock date and minutes of an ISO string, as written (the schedule's own zone). */
export const isoDate = (iso: string) => iso.slice(0, 10);
export const isoMinutes = (iso: string) => toMin(iso.slice(11, 16));
export const isoHHMM = (iso: string) => iso.slice(11, 16);

/** Today's date and minutes since midnight in a time zone. */
export function nowIn(timeZone: string | undefined, ms: number): { date: string; minutes: number } {
  try {
    const p = Object.fromEntries(
      new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
        .formatToParts(new Date(ms))
        .map((x) => [x.type, x.value])
    );
    return { date: `${p.year}-${p.month}-${p.day}`, minutes: Number(p.hour) * 60 + Number(p.minute) };
  } catch {
    const d = new Date(ms);
    return { date: isoDayLocal(d), minutes: d.getHours() * 60 + d.getMinutes() };
  }
}

/** "Thu 6 Nov, 6 pm" for a slot start. */
export function slotWhen(iso: string): string {
  return `${dayLabel(isoDate(iso))}, ${timeWords(isoHHMM(iso))}`;
}
/** "6 to 6:15 pm" */
export function slotSpan(start: string, end: string): string {
  return rangeWords({ start: isoHHMM(start), end: isoHHMM(end) });
}
/** "Today, 6 pm" or "Thu 6 Nov, 6 pm" for the "next free" line. */
export function nextFreeWords(iso: string | null | undefined, today: string): string {
  if (!iso) return "Nothing free soon";
  const d = isoDate(iso);
  if (d === today) return `Today, ${timeWords(isoHHMM(iso))}`;
  if (d === addDays(today, 1)) return `Tomorrow, ${timeWords(isoHHMM(iso))}`;
  return slotWhen(iso);
}

/* ------------------------------------------------------------ the grid */

export type GridRow =
  | { kind: "slot"; time: string; minutes: number }
  | { kind: "gap"; from: string; to: string; fromMin: number; toMin: number };

export interface CalendarGrid {
  rows: GridRow[];
  step: number;
  /** minutes where the first slot row starts */
  first: number;
}

/**
 * The rows of the week grid: one per interval from the earliest opening to the latest closing in view. Stretches where
 * nothing is open on any visible day (lunch, the afternoon, the night) collapse into a single thin band so the grid stays short.
 */
export function buildGrid(days: SlotDay[], slotMinutes: number): CalendarGrid {
  const starts = new Set<number>();
  let last = 0;
  for (const d of days) {
    for (const s of d.slots) {
      starts.add(isoMinutes(s.start));
      const e = isoMinutes(s.end);
      last = Math.max(last, e === 0 ? 1440 : e);
    }
  }
  if (!starts.size) return { rows: [], step: slotMinutes, first: 0 };
  const sorted = [...starts].sort((a, b) => a - b);
  let step = Infinity;
  for (let i = 1; i < sorted.length; i++) step = Math.min(step, sorted[i] - sorted[i - 1]);
  if (!Number.isFinite(step)) step = slotMinutes;
  step = Math.max(5, step);
  // Rows sit on the grid of the earliest start; a start that is off the grid still gets its own row.
  const times = new Set<number>();
  for (let t = sorted[0]; t < last; t += step) times.add(t);
  for (const s of sorted) times.add(s);
  const allTimes = [...times].sort((a, b) => a - b);
  const openAt = (t: number) => days.some((d) => d.slots.some((s) => isoMinutes(s.start) === t));
  const rows: GridRow[] = [];
  let run: number[] = [];
  const flush = () => {
    if (!run.length) return;
    if (run.length >= 3) rows.push({ kind: "gap", from: fromMin(run[0]), to: fromMin(run[run.length - 1] + step), fromMin: run[0], toMin: run[run.length - 1] + step });
    else run.forEach((t) => rows.push({ kind: "slot", time: fromMin(t), minutes: t }));
    run = [];
  };
  for (const t of allTimes) {
    if (openAt(t)) {
      flush();
      rows.push({ kind: "slot", time: fromMin(t), minutes: t });
    } else run.push(t);
  }
  flush();
  return { rows, step, first: sorted[0] };
}

export const ROW_H = 30;
export const GAP_H = 22;

/** Vertical offset of a time of day within the rows (px), or null when it is outside them. */
export function offsetOf(rows: GridRow[], minutes: number, step: number, rowH = ROW_H, gapH = GAP_H): number | null {
  let y = 0;
  for (const r of rows) {
    if (r.kind === "slot") {
      if (minutes >= r.minutes && minutes < r.minutes + step) return y + ((minutes - r.minutes) / step) * rowH;
      y += rowH;
    } else {
      if (minutes >= r.fromMin && minutes < r.toMin) return y + ((minutes - r.fromMin) / (r.toMin - r.fromMin)) * gapH;
      y += gapH;
    }
  }
  return null;
}

export function slotAt(day: SlotDay | undefined, time: string): Slot | undefined {
  return day?.slots.find((s) => isoHHMM(s.start) === time);
}

/* ---------------------------------------------------------------- drag */

export type CellState = SlotStatus | "closed";

/**
 * The cells a press-and-drag marks busy. `states` is one column from top to bottom, `from` is where the press began and
 * `to` where the pointer is now. Only free cells can be marked, so the selection stops before the first cell that is not
 * free (booked, blocked, closed, in the past), in the direction of the drag. Returns `null` when the press began on a cell
 * that cannot be marked.
 */
export function dragSelection(states: readonly CellState[], from: number, to: number): { start: number; end: number } | null {
  if (from < 0 || from >= states.length || states[from] !== "free") return null;
  const target = Math.max(0, Math.min(states.length - 1, to));
  const dir = target >= from ? 1 : -1;
  let reach = from;
  for (let i = from + dir; dir > 0 ? i <= target : i >= target; i += dir) {
    if (states[i] !== "free") break;
    reach = i;
  }
  return { start: Math.min(from, reach), end: Math.max(from, reach) };
}

/** Optimistic copy of a slots response with the slots in `[start, end)` shown as busy. */
export function withBlock(data: SlotsResponse, startIso: string, endIso: string, blockId: string): SlotsResponse {
  const s = Date.parse(startIso);
  const e = Date.parse(endIso);
  return {
    ...data,
    days: data.days.map((d) => ({
      ...d,
      slots: d.slots.map((x) => {
        const t = Date.parse(x.start);
        return t >= s && t < e && x.status === "free" ? { ...x, status: "blocked" as const, remaining: 0, block_id: blockId } : x;
      }),
    })),
  };
}

/** Optimistic copy with one block removed: its slots are free again. */
export function withoutBlock(data: SlotsResponse, blockId: string, capacity: number): SlotsResponse {
  return {
    ...data,
    days: data.days.map((d) => ({
      ...d,
      slots: d.slots.map((x) => (x.block_id === blockId ? { ...x, status: "free" as const, remaining: capacity, block_id: null } : x)),
    })),
  };
}

/** The first user-perceived character: "कुलकर्णी" -> "कु" (a consonant keeps its vowel sign), "Éva" -> "É". */
function firstGrapheme(word: string): string {
  const Seg = (Intl as unknown as { Segmenter?: new (l?: string, o?: { granularity: "grapheme" }) => { segment(s: string): Iterable<{ segment: string }> } }).Segmenter;
  if (Seg) {
    for (const part of new Seg(undefined, { granularity: "grapheme" }).segment(word)) return part.segment;
  }
  const chars = Array.from(word);
  let out = chars[0] ?? "";
  for (let i = 1; i < chars.length && /\p{M}/u.test(chars[i]); i++) out += chars[i];   // keep the combining marks
  return out;
}

/** "Sunita Kulkarni" -> "Sunita K."; "सुनीता कुलकर्णी" -> "सुनीता कु."; one word stays as it is; nothing -> "A caller". */
export function shortName(name: string | null | undefined): string {
  const parts = String(name ?? "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "A caller";
  if (parts.length === 1) return parts[0];
  return `${parts[0]} ${firstGrapheme(parts[parts.length - 1]).toUpperCase()}.`;
}

/** Who is in a slot, for a cell: "Sunita K." or "Sunita K., Ravi M. +1" (the first two, then how many more). */
export function namesLine(bookings: SlotBooking[], shown = 2): string {
  if (!bookings.length) return "";
  const names = bookings.slice(0, shown).map((b) => shortName(b.name));
  return names.join(", ") + (bookings.length > shown ? ` +${bookings.length - shown}` : "");
}

/** "Arjun S. · 2 nights" for a stay on a month grid. */
export function guestLine(b: SlotBooking): string {
  return b.nights && b.nights > 0 ? `${shortName(b.name)} · ${b.nights} night${b.nights === 1 ? "" : "s"}` : shortName(b.name);
}

/** The words on a cell. Colour is never the only signal: each state also has a pattern and a text. */
export function slotLabel(s: Slot, capacity: number): string {
  switch (s.status) {
    case "free":
      return "Free";
    case "booked":
      return namesLine(s.bookings) || `${s.remaining} of ${capacity} left`;
    case "full":
      return namesLine(s.bookings) || "Full";
    case "blocked":
      return "Busy";
    case "past":
      return "Past";
  }
}

/* -------------------------------------------------------------- accents */

export const ACCENTS: Record<string, string> = {
  violet: "#6c62ed",
  orange: "#fe914b",
  blue: "#3aa8c8",
  green: "#079455",
  rose: "#e5588a",
  amber: "#e0a21b",
  teal: "#14a39a",
  slate: "#64748b",
};
export const accentColor = (key: string | null | undefined) => ACCENTS[key ?? ""] ?? ACCENTS.violet;
/** A tint of an accent that works on the light and the dark surface. */
export const accentTint = (key: string | null | undefined, pct = 14) =>
  `color-mix(in srgb, ${accentColor(key)} ${pct}%, var(--color-bg-surface))`;

/* ----------------------------------------------------- small utilities */

/**
 * The front-desk type of an agent for its icon: the `template_id` the server stores, and only for an agent that has none
 * (made before it was stored) the type this browser remembers from creating it. null for a desk made from scratch.
 */
export function templateIdOf(agent: { id: string; template_id?: string | null } | null | undefined): string | null {
  const key = deskKeyOfAgent(agent);
  return key === "custom" ? null : key;
}

export const PREF_KEY = "vakya-bookings-desk";
export function readPref(): string | null {
  try {
    return localStorage.getItem(PREF_KEY);
  } catch {
    return null;
  }
}
export function writePref(value: string): void {
  try {
    localStorage.setItem(PREF_KEY, value);
  } catch {
    /* private window: the choice is simply not remembered */
  }
}
