/**
 * The live call: the events a front desk writes while a call runs (backend/services/call_events.py, documented in
 * docs/ARCHITECTURE.md "Live call events"), and a reducer that turns them into what the split screen shows: the
 * transcript with its tool-call chips, and what the calendar should be doing.
 */

import { formatClock } from "./formatDate";

export type WordLang = "en" | "hi" | "mr" | "x";

interface Envelope {
  seq: number;
  at: string;
  agent_id: string;
  conversation_id: string | null;
  turn_id: string | null;
}

export interface CallerEvent extends Envelope {
  type: "caller";
  text: string;
  typed: boolean;
  language: string;
  script: "latin" | "devanagari" | "mixed" | "other" | "none";
  unsure: boolean;
  words: [string, WordLang][];
}

export type ToolStatus =
  | "checking" | "free" | "taken" | "listing" | "listed" | "booking" | "readback" | "booked" | "refused"
  | "cancelling" | "confirm" | "cancelled" | "finding" | "found" | "failed";

export interface SlotOption {
  date: string;
  time?: string;
}

export interface ToolEvent extends Envelope {
  type: "tool";
  tool: "check_availability" | "list_free_slots" | "book" | "cancel" | "find_booking";
  call_id: string;
  phase: "start" | "end";
  status: ToolStatus;
  schedule_id: string | null;
  schedule: string | null;
  date?: string;
  time?: string;
  label: string;
  available?: boolean;
  reason?: string | null;
  options?: SlotOption[];
  free?: string[];
  booking_id?: string;
}

export interface AgentEvent extends Envelope {
  type: "agent";
  index: number;
  text: string;
  audio_ms: number | null;
  readback: boolean;
  disclosure: boolean;
  language: string;
  script?: "latin" | "devanagari" | "mixed" | "other" | "none";
  words: [string, WordLang][];
}

export interface BookingEvent extends Envelope {
  type: "booking";
  booking_id: string;
  name: string | null;
  schedule_id: string | null;
  schedule: string | null;
  date: string;
  time: string;
  start: string;
  evidence_id: string | null;
  receipt_id: string | null;
  evidence_complete: boolean | null;
}

export interface TurnEndEvent extends Envelope {
  type: "turn_end";
  language?: string;
  tool_calls?: number;
  error?: string;
}

/** One phone turn's response time, measured on the call: the caller's last word to the first audio of the reply. */
export interface LatencyEvent extends Envelope {
  type: "latency";
  turn?: number;
  response_ms: number;
  end_wait?: string;
}

export type LiveEvent = CallerEvent | ToolEvent | AgentEvent | BookingEvent | TurnEndEvent | LatencyEvent;

export interface EventsResponse {
  events: LiveEvent[];
  last_seq: number;
  head: number;
}

/* ------------------------------------------------------------------------------------------ the screen's state */

export type Line =
  | { kind: "caller"; id: string; event: CallerEvent }
  | { kind: "agent"; id: string; event: AgentEvent; playFrom: number; playUntil: number }
  | { kind: "tool"; id: string; start: ToolEvent | null; end: ToolEvent | null };

/** What the calendar is doing. Times are "HH:MM" on `date`. */
export interface CalendarFocus {
  scheduleId: string | null;
  date: string | null;
  /** The time being checked, with its result once it is in. */
  checking: { time: string; state: "checking" | "free" | "taken" } | null;
  /** Times offered instead (the nearest free ones), possibly on another date. */
  offered: SlotOption[];
  /** Read back to the caller, waiting for a yes. */
  holding: { date: string; time: string } | null;
  booked: { date: string; time: string; bookingId: string; at: number } | null;
}

export interface LiveState {
  lines: Line[];
  focus: CalendarFocus;
  conversationId: string | null;
  lastSeq: number;
  /** Set when a booking was just made (the toast); cleared by the screen. */
  justBooked: { bookingId: string; name: string | null; date: string; time: string; evidenceId: string | null; receiptId: string | null; at: number } | null;
  /** The agent is speaking until this clock time (ms), from the length of its sentence. */
  speakingUntil: number;
  /** Ended turns count, to know a call has gone quiet. */
  turnsEnded: number;
  /** Response time of each answered phone turn, oldest first (ms, measured on the call). */
  responses: number[];
}

export const emptyFocus = (): CalendarFocus => ({ scheduleId: null, date: null, checking: null, offered: [], holding: null, booked: null });
export const initialLive = (): LiveState => ({
  lines: [], focus: emptyFocus(), conversationId: null, lastSeq: 0, justBooked: null, speakingUntil: 0, turnsEnded: 0,
  responses: [],
});

const MAX_LINES = 80;

function applyTool(f: CalendarFocus, e: ToolEvent): CalendarFocus {
  const base = { ...f, scheduleId: e.schedule_id ?? f.scheduleId };
  if (e.tool === "check_availability") {
    const date = e.date ?? f.date;
    if (e.phase === "start") return { ...base, date, checking: e.time ? { time: e.time, state: "checking" } : null, offered: [], holding: null };
    if (e.status === "free") return { ...base, date, checking: e.time ? { time: e.time, state: "free" } : null, offered: [] };
    if (e.status === "taken") {
      return { ...base, date, checking: e.time ? { time: e.time, state: "taken" } : null, offered: e.options ?? [] };
    }
    return { ...base, date, checking: null };
  }
  if (e.tool === "list_free_slots") return { ...base, date: e.date ?? f.date };
  if (e.tool === "book") {
    if (e.status === "readback" && e.date && e.time) return { ...base, date: e.date, holding: { date: e.date, time: e.time }, offered: [], checking: null };
    if (e.status === "refused") return { ...base, offered: e.options ?? [], holding: null };
  }
  return base;
}

export function reduceLive(state: LiveState, events: LiveEvent[], now: number = Date.now()): LiveState {
  let s = state;
  for (const e of events) {
    if (e.seq <= s.lastSeq) continue;
    // A different conversation is a different call: the screen starts again, and one opened late shows only the latest call.
    if (e.conversation_id && s.conversationId && e.conversation_id !== s.conversationId) s = { ...initialLive(), lastSeq: s.lastSeq };
    s = { ...s, lastSeq: e.seq, conversationId: e.conversation_id ?? s.conversationId };
    switch (e.type) {
      case "caller":
        s = { ...s, lines: [...s.lines, { kind: "caller" as const, id: `c${e.seq}`, event: e }].slice(-MAX_LINES) };
        break;
      case "agent":
        {
          const playFrom = Math.max(s.speakingUntil, now);   // sentences play one after another
          const playUntil = playFrom + Math.max(600, e.audio_ms ?? 1500);
          s = { ...s, lines: [...s.lines, { kind: "agent" as const, id: `a${e.seq}`, event: e, playFrom, playUntil }].slice(-MAX_LINES), speakingUntil: playUntil };
        }
        break;
      case "tool": {
        const lines = [...s.lines];
        const i = lines.findIndex((l) => l.kind === "tool" && l.id === e.call_id);
        if (i >= 0) {
          const old = lines[i] as Extract<Line, { kind: "tool" }>;
          lines[i] = e.phase === "start" ? { ...old, start: e } : { ...old, end: e };
        } else {
          lines.push({ kind: "tool", id: e.call_id, start: e.phase === "start" ? e : null, end: e.phase === "end" ? e : null });
        }
        s = { ...s, lines: lines.slice(-MAX_LINES), focus: applyTool(s.focus, e) };
        if (e.tool === "book" && e.status === "booked" && e.date && e.time && e.booking_id) {
          s = { ...s, focus: { ...s.focus, holding: null, checking: null, offered: [], booked: { date: e.date, time: e.time, bookingId: e.booking_id, at: now } } };
        }
        break;
      }
      case "booking":
        s = {
          ...s,
          focus: { ...s.focus, scheduleId: e.schedule_id ?? s.focus.scheduleId, date: e.date, holding: null, checking: null, offered: [],
                   booked: { date: e.date, time: e.time, bookingId: e.booking_id, at: now } },
          justBooked: { bookingId: e.booking_id, name: e.name, date: e.date, time: e.time, evidenceId: e.evidence_id, receiptId: e.receipt_id, at: now },
        };
        break;
      case "turn_end":
        s = { ...s, turnsEnded: s.turnsEnded + 1 };
        break;
      case "latency":
        if (Number.isFinite(e.response_ms)) s = { ...s, responses: [...s.responses, e.response_ms].slice(-40) };
        break;
    }
  }
  return s;
}

/** Fold the booking's tool "booked" into a toast even if the `booking` event (with the evidence) has not come yet. */
export const clearToast = (s: LiveState): LiveState => (s.justBooked ? { ...s, justBooked: null } : s);

/* ---------------------------------------------------------------------------------------------- words and times */

export const LANG_NAME: Record<string, string> = { en: "English", hi: "Hindi", mr: "Marathi", hinglish: "Hinglish", x: "Language not sure" };

/** "17:30" -> "5:30 pm". */
export function clock(hhmm: string): string {
  return formatClock(hhmm) ?? hhmm;
}

/** The label under a transcript line: what language was heard, and whether that is a guess. */
export function languageNote(language: string, script: string, unsure: boolean): string {
  const name = LANG_NAME[language] ?? language.toUpperCase();
  const where = script === "devanagari" ? "Devanagari" : script === "latin" ? "Latin" : script === "mixed" ? "Devanagari and Latin" : "";
  return `${unsure ? "Probably " : ""}${name}${where && language !== "en" ? ` · ${where}` : ""}`;
}
