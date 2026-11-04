import type { CallChannel, CallOutcomeKey, CallSummary } from "./types";
import type { BadgeTone } from "@/components/ui/Badge";
import {
  DEFAULT_TIMEZONE,
  dayOfInstant,
  formatClock,
  formatDateTime,
  formatDay,
  formatDayTime,
  formatTime,
} from "@/lib/formatDate";

export { DEFAULT_TIMEZONE };

export const CHANNEL_LABEL: Record<CallChannel, string> = {
  phone: "Phone",
  website: "Website",
  whatsapp: "WhatsApp",
  playground: "In the app",
};

/** The channel as the Calls list names it: a call the receptionist placed after a missed call is its own kind. */
export type ChannelKind = "phone" | "callback" | "browser" | "practice" | "whatsapp";
export const CHANNEL_KIND_LABEL: Record<ChannelKind, string> = {
  phone: "Phone",
  callback: "Missed-call callback",
  browser: "Browser",
  practice: "Practice",
  whatsapp: "WhatsApp",
};
export const CHANNEL_KIND_ORDER: ChannelKind[] = ["phone", "callback", "browser", "practice"];

export function channelKind(c: Pick<CallSummary, "channel" | "called_back">): ChannelKind {
  if (c.channel === "phone") return c.called_back ? "callback" : "phone";
  return c.channel === "website" ? "browser" : c.channel === "playground" ? "practice" : "whatsapp";
}

export const OUTCOME_LABEL: Record<CallOutcomeKey, string> = {
  booked: "Booked",
  answered: "Answered",
  needs_approval: "Needs approval",
  couldnt_answer: "Couldn't answer",
  ended_early: "Ended early",
  handed_off: "Needs a person",
  declined: "Request declined",
};

export const OUTCOME_TONE: Record<CallOutcomeKey, BadgeTone> = {
  booked: "success",
  answered: "secondary",
  needs_approval: "warning",
  couldnt_answer: "error",
  ended_early: "neutral",
  handed_off: "warning",
  declined: "neutral",
};

/** Order of the filter chips: the ones that need the owner first. */
export const OUTCOME_ORDER: CallOutcomeKey[] = ["needs_approval", "handed_off", "booked", "answered", "couldnt_answer", "ended_early", "declined"];

/** "Today, 10:42 am", "Yesterday, 4:05 pm", "Mon 12 Oct, 10:42 am" in the desk's time zone. */
export function whenLabel(iso: string | null, now: Date = new Date(), tz?: string | null): string {
  const text = formatDateTime(iso, tz, now);
  if (!iso || !text) return "Unknown time";
  const day = dayOfInstant(iso, tz);
  const today = dayOfInstant(now, tz);
  const time = formatTime(iso, tz);
  if (day && today && day === today) return `Today, ${time}`;
  if (day && today && day === addDays(today, -1)) return `Yesterday, ${time}`;
  return text;
}

/** "45 s", "2 min 05 s". */
export function durationLabel(seconds: number | null): string | null {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const m = Math.floor(seconds / 60);
  return `${m} min ${String(Math.round(seconds % 60)).padStart(2, "0")} s`;
}

/** Who was on the other end, in words. Numbers arrive masked from the server and are never unmasked here. */
export function callerLabel(c: Pick<CallSummary, "channel" | "caller">): string {
  if (c.caller) return c.caller;
  if (c.channel === "phone") return "Number not recorded";
  if (c.channel === "website") return "Website visitor";
  if (c.channel === "playground") return "You";
  return "Unknown caller";
}

/** "••••3210": a phone number reduced to its last four digits, for screens that only need to tell callers apart. */
export function maskNumber(value: string | null | undefined): string {
  const digits = String(value ?? "").replace(/\D/g, "");
  return digits.length >= 5 ? `••••${digits.slice(-4)}` : digits ? "••••" : "Number not given";
}

const LANGUAGE_NAME: Record<string, string> = { hi: "Hindi", mr: "Marathi", en: "English", hinglish: "Hinglish", ta: "Tamil", te: "Telugu", bn: "Bengali", gu: "Gujarati", kn: "Kannada", ml: "Malayalam", pa: "Punjabi" };

/** "Hindi" for "hi"; an unknown code is shown as it is. */
export function languageLabel(code: string | null | undefined): string | null {
  const c = (code ?? "").trim().toLowerCase();
  return c ? (LANGUAGE_NAME[c] ?? c) : null;
}

/** "Tue 10 Nov, 4 pm" for a booking's date (YYYY-MM-DD) and time (HH:MM); either may be missing. */
export function bookingLabel(b: { date?: string; time?: string } | null | undefined): string | null {
  if (!b) return null;
  return formatDayTime(b.date, b.time);
}

export const CALLER_OUTCOME_LABEL: Record<string, string> = {
  booked: "Booked an appointment",
  asked: "Asked a question and got an answer",
  unanswered: "Something could not be answered",
};

// ------------------------------------------------------------------------------------------------ time zone and days

/** "2026-11-04": the calendar day of an instant in the desk's time zone. */
export function dayKey(iso: string | number | Date | null | undefined, tz: string = DEFAULT_TIMEZONE): string | null {
  return dayOfInstant(iso, tz);
}

/** The day `n` days from a YYYY-MM-DD day (negative: earlier), as YYYY-MM-DD. */
export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** "Thu 12 Nov" for a YYYY-MM-DD day; with the year when it is not `thisYear`. */
export function dayWords(day: string, thisYear?: string): string {
  return formatDay(day, thisYear ? Number(thisYear) : undefined) ?? day;
}

/** "Today", "Yesterday" or "Mon 2 Nov" for a YYYY-MM-DD day, against today's day. */
export function dayHeading(day: string, today: string): string {
  if (day === today) return "Today";
  if (day === addDays(today, -1)) return "Yesterday";
  return dayWords(day, today.slice(0, 4));
}

/** "7:42 pm" in the desk's time zone. */
export function clockLabel(iso: string | null | undefined, tz: string = DEFAULT_TIMEZONE): string {
  return formatTime(iso, tz);
}

/** "7 pm", "5:30 pm" for an HH:MM time (the minutes only when there are some). */
export function clockWords(hhmm: string | null | undefined): string | null {
  return formatClock(hhmm);
}

/** "A56 · Thu 12 Nov, 7 pm" for a booking (the id upper case, the day and the 12-hour time in words). */
export function bookingText(b: { id?: string | null; date?: string | null; time?: string | null } | null | undefined): string | null {
  if (!b || !b.id) return null;
  const when = formatDayTime(b.date && /^\d{4}-\d{2}-\d{2}$/.test(b.date) ? b.date : null, /^\d{1,2}:\d{2}$/.test(b.time ?? "") ? b.time : null) ?? "";
  return when ? `${b.id.toUpperCase()} · ${when}` : b.id.toUpperCase();
}

/** "2:05" or "45 s" for a Duration column. */
export function durationShort(seconds: number | null): string | null {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

/** The caller as one line: the name, else the masked number, else words for who it must be. `muted` marks "no number" style fallbacks. */
export function callerCell(c: Pick<CallSummary, "channel" | "caller" | "caller_name">, profileName?: string | null, profileNumber?: string | null): { text: string; muted: boolean } {
  const name = (c.caller_name || profileName || "").trim();
  if (name) return { text: name, muted: false };
  const number = c.caller || profileNumber;
  if (number) return { text: number, muted: false };
  if (c.channel === "phone") return { text: "No number", muted: true };
  if (c.channel === "playground") return { text: "You", muted: false };
  return { text: "Unknown", muted: true };
}

/** "Front desk deleted" for the placeholder the server uses when the desk is gone. */
export function deskCell(name: string): { text: string; muted: boolean } {
  return /^deleted agent$/i.test(name.trim()) ? { text: "Front desk deleted", muted: true } : { text: name, muted: false };
}

// ------------------------------------------------------------------------------------------------ the time range

export type RangeKey = "today" | "yesterday" | "7d" | "30d";
export const RANGE_LABEL: Record<RangeKey, string> = { today: "Today", yesterday: "Yesterday", "7d": "Last 7 days", "30d": "Last 30 days" };
export const RANGE_KEYS = Object.keys(RANGE_LABEL) as RangeKey[];
export const DEFAULT_RANGE: RangeKey = "7d";

export type TimeRange = { kind: "preset"; key: RangeKey } | { kind: "custom"; from: string; to: string };

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The range a link asks for: ?range=today, or ?from=&to=; anything unreadable is the default. */
export function parseRange(get: (name: string) => string | null | undefined): TimeRange {
  const r = get("range");
  if (r && (RANGE_KEYS as string[]).includes(r)) return { kind: "preset", key: r as RangeKey };
  const from = get("from");
  const to = get("to");
  if ((from && DAY.test(from)) || (to && DAY.test(to))) {
    const f = from && DAY.test(from) ? from : "";
    const t = to && DAY.test(to) ? to : "";
    return { kind: "custom", from: f, to: t };
  }
  return { kind: "preset", key: DEFAULT_RANGE };
}

/** The query string for the page's own URL (empty for the default range, so the plain link stays clean). */
export function rangeQuery(r: TimeRange): string {
  if (r.kind === "preset") return r.key === DEFAULT_RANGE ? "" : `range=${r.key}`;
  return [r.from && `from=${r.from}`, r.to && `to=${r.to}`].filter(Boolean).join("&");
}

/** The query for GET /api/calls. */
export function rangeApiQuery(r: TimeRange): string {
  return r.kind === "preset" ? `range=${r.key}` : [r.from && `from=${r.from}`, r.to && `to=${r.to}`].filter(Boolean).join("&") || `range=${DEFAULT_RANGE}`;
}

/** The first and last day (YYYY-MM-DD) a range covers, for the date inputs. */
export function rangeDays(r: TimeRange, today: string): { from: string; to: string } {
  if (r.kind === "custom") {
    const to = r.to || today;
    return { from: r.from || addDays(to, -29), to };
  }
  if (r.key === "today") return { from: today, to: today };
  if (r.key === "yesterday") return { from: addDays(today, -1), to: addDays(today, -1) };
  return { from: addDays(today, r.key === "7d" ? -6 : -29), to: today };
}
