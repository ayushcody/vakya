/**
 * One way to write a day and a time in the desk, in Indian English: "Thu 12 Nov", "7 pm", "7:30 pm", "Thu 12 Nov, 7 pm".
 * Twelve-hour clock with a lower-case am or pm, no leading zero, no ":00" on the hour, the day before the month, no
 * comma after the weekday, no year unless it is not this year. Instants are shown in the desk's time zone
 * (VAKYA_TIMEZONE of the server, Asia/Kolkata by default), never the browser's, so a call looks the same on every screen.
 */

/** Used until the server says which time zone the desk keeps (VAKYA_TIMEZONE). */
export const DEFAULT_TIMEZONE = process.env.NEXT_PUBLIC_VAKYA_TIMEZONE || "Asia/Kolkata";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;
const WEEKDAYS_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"] as const;
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"] as const;

/** `tz` when the runtime knows it, else the default. */
export function deskTimezone(tz?: string | null): string {
  if (!tz) return DEFAULT_TIMEZONE;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return tz;
  } catch {
    return DEFAULT_TIMEZONE;
  }
}

export interface Parts {
  year: number;
  month: number; // 1-12
  day: number;
  weekday: number; // 0 = Sunday
  hour: number; // 0-23
  minute: number;
}

/** The wall-clock parts of an instant in a time zone. */
export function wallClock(instant: Date, tz?: string | null): Parts {
  return partsOf(instant, deskTimezone(tz));
}

function partsOf(instant: Date, tz: string): Parts {
  const f = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", hourCycle: "h23",
  });
  const get: Record<string, number> = {};
  for (const p of f.formatToParts(instant)) if (p.type !== "literal") get[p.type] = Number(p.value);
  const weekday = new Date(Date.UTC(get.year, get.month - 1, get.day)).getUTCDay();
  return { year: get.year, month: get.month, day: get.day, weekday, hour: (get.hour ?? 0) % 24, minute: get.minute ?? 0 };
}

/** A YYYY-MM-DD day as parts (no time zone: it is a calendar day). */
function dayParts(day: string): Parts | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(Date.UTC(y, mo - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return null;
  return { year: y, month: mo, day: d, weekday: date.getUTCDay(), hour: 0, minute: 0 };
}

function clockOf(hour: number, minute: number): string {
  return `${hour % 12 || 12}${minute ? `:${String(minute).padStart(2, "0")}` : ""} ${hour < 12 ? "am" : "pm"}`;
}

function dayOf(p: Parts, thisYear?: number): string {
  const text = `${WEEKDAYS[p.weekday]} ${p.day} ${MONTHS[p.month - 1]}`;
  return thisYear != null && p.year !== thisYear ? `${text} ${p.year}` : text;
}

/** "7 pm", "5:30 pm" for an HH:MM time of day; null when it is not one. */
export function formatClock(hhmm: string | null | undefined): string | null {
  const m = /^(\d{1,2}):(\d{2})/.exec(hhmm ?? "");
  if (!m) return null;
  const [h, min] = [Number(m[1]), Number(m[2])];
  return h > 23 || min > 59 ? null : clockOf(h, min);
}

/** "Thu 12 Nov" for a YYYY-MM-DD day; the year is added when `thisYear` is given and differs. */
export function formatDay(day: string | null | undefined, thisYear?: number): string | null {
  const p = day ? dayParts(day) : null;
  return p ? dayOf(p, thisYear) : null;
}

/** "Thursday 12 November" for a YYYY-MM-DD day (headings and spoken labels); the day itself when it is not one. */
export function formatDayLong(day: string): string {
  const p = dayParts(day);
  return p ? `${WEEKDAYS_LONG[p.weekday]} ${p.day} ${MONTHS_LONG[p.month - 1]}` : day;
}

/** "November 2026" for a YYYY-MM-DD day; the day itself when it is not one. */
export function formatMonthYear(day: string): string {
  const p = dayParts(day);
  return p ? `${MONTHS_LONG[p.month - 1]} ${p.year}` : day;
}

/** "9 to 15 Nov 2026" or "28 Oct to 3 Nov 2026" for two YYYY-MM-DD days, the second no earlier than the first. */
export function formatDayRange(from: string, to: string): string {
  const a = dayParts(from);
  const b = dayParts(to);
  if (!a || !b) return `${from} to ${to}`;
  const left = a.month === b.month && a.year === b.year ? `${a.day}` : `${a.day} ${MONTHS[a.month - 1]}`;
  return `${left} to ${b.day} ${MONTHS[b.month - 1]} ${b.year}`;
}

/** "Thu 12 Nov, 7 pm" for a YYYY-MM-DD day and an HH:MM time (either may be missing); null when both are. */
export function formatDayTime(day: string | null | undefined, hhmm?: string | null): string | null {
  const text = [formatDay(day), formatClock(hhmm)].filter(Boolean).join(", ");
  return text || null;
}

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value == null || value === "") return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "7 pm" or "7:42 pm" for an instant, in the desk's time zone; "" when there is no instant. */
export function formatTime(value: string | number | Date | null | undefined, tz?: string | null): string {
  const d = toDate(value);
  if (!d) return "";
  const p = partsOf(d, deskTimezone(tz));
  return clockOf(p.hour, p.minute);
}

/** "Thu 12 Nov" for an instant in the desk's time zone, with the year when it is not this year; "" when there is none. */
export function formatDate(value: string | number | Date | null | undefined, tz?: string | null, now: Date = new Date()): string {
  const d = toDate(value);
  if (!d) return "";
  const zone = deskTimezone(tz);
  return dayOf(partsOf(d, zone), partsOf(now, zone).year);
}

/** "Thu 12 Nov, 7 pm" for an instant in the desk's time zone; "" when there is none. */
export function formatDateTime(value: string | number | Date | null | undefined, tz?: string | null, now: Date = new Date()): string {
  const d = toDate(value);
  if (!d) return "";
  const zone = deskTimezone(tz);
  const p = partsOf(d, zone);
  return `${dayOf(p, partsOf(now, zone).year)}, ${clockOf(p.hour, p.minute)}`;
}

/** The YYYY-MM-DD day of an instant in the desk's time zone, or null. */
export function dayOfInstant(value: string | number | Date | null | undefined, tz?: string | null): string | null {
  const d = toDate(value);
  if (!d) return null;
  const p = partsOf(d, deskTimezone(tz));
  return `${p.year}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`;
}
