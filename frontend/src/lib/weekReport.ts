/**
 * This week: what the front desk did in the last seven days, worked out in the browser from what Vakya already stores
 * (GET /api/calls, GET /api/bookings, GET /api/schedules). Nothing is estimated: every number is a count of stored rows.
 * Pure functions only, so every rule here is tested without rendering (lib/__tests__/weekReport.test.ts).
 * Contract: docs/DESK_FEATURES.md.
 */

import type { CallOutcomeKey, CallSummary } from "@/app/(app)/desk/calls/types";
import type { Booking } from "@/components/desk/calls";
import { DAY_KEYS, toMin, type DayKey, type Schedule } from "@/lib/schedules";
import { deskTimezone, wallClock } from "@/lib/formatDate";
import { OUTCOME_LABEL, addDays, languageLabel as callLanguage } from "@/app/(app)/desk/calls/format";

export const WEEK_DAYS = 7;
/** Rows of the heatmap when no call or opening hour asks for more: 8 am to 9 pm. */
const DEFAULT_FIRST_HOUR = 8;
const DEFAULT_LAST_HOUR = 21;
const MAX_ATTENTION = 6;

/** The order the outcomes are listed in: the good ends first, then what needs a person. */
export const OUTCOME_ORDER: CallOutcomeKey[] = ["booked", "answered", "needs_approval", "handed_off", "declined", "couldnt_answer", "ended_early"];
/** Ends of a call the owner may want to look at. */
const ATTENTION: ReadonlySet<CallOutcomeKey> = new Set(["couldnt_answer", "ended_early", "handed_off", "needs_approval"]);

export interface HeatCell {
  count: number;
  /** True inside the opening hours, false outside, null when no hours are set. */
  open: boolean | null;
}

export interface WeekReport {
  /** The seven days, oldest first (YYYY-MM-DD in the desk's time zone); the last one is today. */
  days: string[];
  /** Calls answered (phone and web calls, not practice calls, not calls where nobody spoke). */
  calls: number;
  previousCalls: number;
  /** Of those, the calls that came while every schedule was closed; null when no schedule has opening hours. */
  afterHours: number | null;
  /** Appointments made by the front desk (not added by staff) in the seven days. */
  booked: number;
  previousBooked: number;
  /** Minutes on the phone, from the stored durations; null when no call has one. */
  minutes: number | null;
  /** cells[day][row]: rows are hours `firstHour`..`lastHour`. */
  cells: HeatCell[][];
  firstHour: number;
  lastHour: number;
  maxCell: number;
  busiest: { day: string; hour: number; count: number } | null;
  outcomes: { key: CallOutcomeKey; label: string; count: number }[];
  languages: { code: string; label: string; count: number }[];
  attention: CallSummary[];
  /** The calls list was cut at its limit, so the counts are a floor. */
  truncated: boolean;
}

interface When {
  day: string;
  dow: DayKey;
  minute: number;
}

/** The desk-time day, weekday and minute of the day of an ISO instant, or null. */
export function whenOf(iso: string | null | undefined, tz?: string | null): When | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const p = wallClock(d, tz);
  return {
    day: `${p.year}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`,
    dow: DAY_KEYS[(p.weekday + 6) % 7],
    minute: p.hour * 60 + p.minute,
  };
}

/** Schedules with weekly opening hours (time slots; a hotel's nights are not opening hours). */
function hourSchedules(schedules: Schedule[]): Schedule[] {
  return schedules.filter((s) => s.kind === "slots" && DAY_KEYS.some((k) => (s.week?.[k] ?? []).length > 0));
}

/** True when any schedule is open at that minute of that day; null when there are no opening hours to go by. */
export function isOpen(schedules: Schedule[], day: string, dow: DayKey, minute: number): boolean | null {
  const withHours = hourSchedules(schedules);
  if (!withHours.length) return null;
  return withHours.some(
    (s) => !(s.closed_dates ?? []).includes(day) && (s.week[dow] ?? []).some((r) => minute >= toMin(r.start) && minute < toMin(r.end))
  );
}

/** A call the week counts: a phone or web call with a real caller (not a practice call, not one where nobody spoke). */
export function countsAsCall(c: CallSummary): boolean {
  return !c.practice && !c.empty && (c.channel === "phone" || c.channel === "website");
}

/** "Hindi" for "hi" (the Calls page's names); "Not detected" when the call has no language. */
export function languageLabel(code: string | null | undefined): string {
  return callLanguage(code) ?? "Not detected";
}

/** A booking the front desk made (staff additions and practice calls do not count). */
function madeByDesk(b: Booking): boolean {
  return !b.practice && b.source !== "staff";
}

export function buildWeek(input: {
  calls: CallSummary[];
  bookings: Booking[];
  schedules: Schedule[];
  timezone?: string | null;
  now?: Date;
  truncated?: boolean;
}): WeekReport {
  const tz = deskTimezone(input.timezone);
  const now = input.now ?? new Date();
  const today = whenOf(now.toISOString(), tz)!.day;
  const days = Array.from({ length: WEEK_DAYS }, (_, i) => addDays(today, -(WEEK_DAYS - 1 - i)));
  const first = days[0];
  const prevFirst = addDays(first, -WEEK_DAYS);
  const inWeek = (day: string) => day >= first && day <= today;
  const inPrevious = (day: string) => day >= prevFirst && day < first;

  const week: { call: CallSummary; when: When }[] = [];
  let previousCalls = 0;
  for (const call of input.calls) {
    if (!countsAsCall(call)) continue;
    const when = whenOf(call.when, tz);
    if (!when) continue;
    if (inWeek(when.day)) week.push({ call, when });
    else if (inPrevious(when.day)) previousCalls++;
  }

  const hasHours = hourSchedules(input.schedules).length > 0;
  let afterHours = 0;
  let firstHour = DEFAULT_FIRST_HOUR;
  let lastHour = DEFAULT_LAST_HOUR;
  for (const { when } of week) {
    const h = Math.floor(when.minute / 60);
    firstHour = Math.min(firstHour, h);
    lastHour = Math.max(lastHour, h);
    if (hasHours && isOpen(input.schedules, when.day, when.dow, when.minute) === false) afterHours++;
  }
  for (const s of hourSchedules(input.schedules)) {
    for (const k of DAY_KEYS) {
      for (const r of s.week[k] ?? []) {
        firstHour = Math.min(firstHour, Math.floor(toMin(r.start) / 60));
        lastHour = Math.max(lastHour, Math.ceil(toMin(r.end) / 60) - 1);
      }
    }
  }
  firstHour = Math.max(0, firstHour);
  lastHour = Math.min(23, lastHour);

  const cells: HeatCell[][] = days.map((day) => {
    const dow = whenOf(`${day}T12:00:00Z`, "UTC")!.dow;
    return Array.from({ length: lastHour - firstHour + 1 }, (_, i) => ({
      count: 0,
      open: isOpen(input.schedules, day, dow, (firstHour + i) * 60 + 30),
    }));
  });
  let maxCell = 0;
  let busiest: WeekReport["busiest"] = null;
  for (const { when } of week) {
    const cell = cells[days.indexOf(when.day)][Math.floor(when.minute / 60) - firstHour];
    cell.count++;
    if (cell.count > maxCell) {
      maxCell = cell.count;
      busiest = { day: when.day, hour: Math.floor(when.minute / 60), count: cell.count };
    }
  }

  const outcomeCounts = new Map<CallOutcomeKey, { label: string; count: number }>();
  const languageCounts = new Map<string, number>();
  let seconds = 0;
  let timed = 0;
  for (const { call } of week) {
    const o = outcomeCounts.get(call.outcome.key) ?? { label: OUTCOME_LABEL[call.outcome.key] ?? call.outcome.label, count: 0 };
    o.count++;
    outcomeCounts.set(call.outcome.key, o);
    const lang = call.language ? call.language.toLowerCase() : "";
    languageCounts.set(lang, (languageCounts.get(lang) ?? 0) + 1);
    if (call.duration_s != null) {
      seconds += call.duration_s;
      timed++;
    }
  }
  const outcomes = OUTCOME_ORDER.filter((k) => outcomeCounts.has(k)).map((key) => ({ key, ...outcomeCounts.get(key)! }));
  const languages = [...languageCounts.entries()]
    .map(([code, count]) => ({ code, label: languageLabel(code), count }))
    .sort((a, b) => (a.code === "" ? 1 : b.code === "" ? -1 : b.count - a.count));

  let booked = 0;
  let previousBooked = 0;
  for (const b of input.bookings) {
    if (b.status !== "booked" || !madeByDesk(b)) continue;
    const when = whenOf(b.createdAt, tz);
    if (!when) continue;
    if (inWeek(when.day)) booked++;
    else if (inPrevious(when.day)) previousBooked++;
  }

  const attention = week
    .filter(({ call }) => ATTENTION.has(call.outcome.key))
    .sort((a, b) => (a.call.when! < b.call.when! ? 1 : -1))
    .slice(0, MAX_ATTENTION)
    .map(({ call }) => call);

  return {
    days,
    calls: week.length,
    previousCalls,
    afterHours: hasHours ? afterHours : null,
    booked,
    previousBooked,
    minutes: timed ? Math.round(seconds / 60) : null,
    cells,
    firstHour,
    lastHour,
    maxCell,
    busiest,
    outcomes,
    languages,
    attention,
    truncated: !!input.truncated,
  };
}

/** "up from 12", "down from 30", "the same as", or null when there is nothing to compare with. */
export function versusLastWeek(now: number, before: number): string | null {
  if (before === 0) return null;
  if (now === before) return `the same as the week before`;
  return `${now > before ? "up" : "down"} from ${before} the week before`;
}

/** "7 pm" for an hour of the day. */
export function hourLabel(hour: number): string {
  return `${hour % 12 || 12} ${hour < 12 ? "am" : "pm"}`;
}
