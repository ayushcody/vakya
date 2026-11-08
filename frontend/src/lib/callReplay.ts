/**
 * A finished call as the same events a live call writes, so the one live view can replay it. The source is GET /api/calls/{id}:
 * its transcript turns carry `actions` (name, ok, a short summary) for what the agent did on that turn. Nothing here is
 * invented: a field the record does not hold (how long the first audio took) is left out, not guessed.
 */

import type { AgentEvent, CallerEvent, LiveEvent, ToolEvent, ToolStatus, WordLang } from "./liveCall";
import { formatDayTime } from "./formatDate";

export interface ReplayAction {
  name: string;
  ok: boolean;
  summary: string;
}
export interface ReplayTurn {
  role: "caller" | "agent";
  text: string;
  at: string | null;
  actions: ReplayAction[];
}

const DEVANAGARI = /[ऀ-ॿ]/;
const BOOKING_ID = /\b([A-Z]\d{1,6})\b/;
const WHEN = /(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/;
const TIME_FIRST = /^(\d{2}:\d{2}) on (\d{4}-\d{2}-\d{2})/;

function tag(text: string): [string, WordLang][] {
  return text.split(/\s+/).filter(Boolean).map((w) => [w, DEVANAGARI.test(w) ? "hi" : /[A-Za-z]/.test(w) ? "en" : "x"] as [string, WordLang]);
}

/** What one recorded action means on screen: a status, a one-line label and what it touched. */
export function describeAction(a: ReplayAction): Pick<ToolEvent, "status" | "label" | "date" | "time" | "booking_id" | "available"> {
  const s = a.summary || "";
  const booking = BOOKING_ID.exec(s)?.[1];
  const tf = TIME_FIRST.exec(s);
  const w = WHEN.exec(s);
  const date = tf?.[2] ?? w?.[1];
  const time = tf?.[1] ?? w?.[2];
  const when = date ? (formatDayTime(date, time) ?? "") : "";
  if (!a.ok) {
    if (/read this back|not been read back/i.test(s)) return { status: "readback", label: "Read the details back first", date, time };
    return { status: "failed", label: "Could not do that", date, time };
  }
  switch (a.name) {
    case "check_availability": {
      const free = /\bis free\b/.test(s);
      return { status: free ? "free" : "taken", label: `Checked${when ? ` ${when}` : ""}: ${free ? "free" : "not free"}`, date, time, available: free };
    }
    case "list_free_slots":
      return { status: "listed", label: `Looked at free times${when ? ` on ${when}` : ""}`, date, time };
    case "book":
      return { status: "booked", label: ["Booked", booking].filter(Boolean).join(" ") + (when ? ` · ${when}` : ""), date, time, booking_id: booking };
    case "reschedule":
    case "move":
      return { status: "moved", label: ["Moved", booking].filter(Boolean).join(" ") + (when ? ` to ${when}` : ""), date, time, booking_id: booking };
    case "cancel":
      return { status: "cancelled", label: ["Cancelled", booking].filter(Boolean).join(" ") + (when ? ` · ${when}` : ""), date, time, booking_id: booking };
    case "find_booking":
      return { status: "found", label: s.charAt(0).toUpperCase() + s.slice(1) };
    default:
      return { status: "found", label: a.name.replace(/_/g, " ") };
  }
}

/** Turns of a recorded call to events: tool actions come before the agent's words of the same turn. */
export function turnsToEvents(turns: ReplayTurn[], base: { agent_id: string; conversation_id: string; language?: string | null }): LiveEvent[] {
  const out: LiveEvent[] = [];
  let seq = 0;
  const env = (at: string | null) => ({ seq: ++seq, at: at ?? "", agent_id: base.agent_id, conversation_id: base.conversation_id, turn_id: `t${seq}` });
  const lang = base.language || "en";
  for (const t of turns) {
    const text = (t.text ?? "").trim();
    if (t.role === "caller") {
      if (!text || /^\[.*\]$/.test(text)) continue; // "[call connected]" is not something anyone said
      const dev = DEVANAGARI.test(text);
      const e: CallerEvent = { ...env(t.at), type: "caller", text, typed: false, language: dev ? "hi" : lang, script: dev ? "devanagari" : "latin", unsure: false, words: tag(text) };
      out.push(e);
      continue;
    }
    let readback = false;
    t.actions.forEach((a, i) => {
      const d = describeAction(a);
      if (d.status === "readback") readback = true;
      const e: ToolEvent = {
        ...env(t.at), type: "tool", tool: a.name as ToolEvent["tool"], call_id: `r${seq}-${i}`, phase: "end", status: d.status as ToolStatus,
        schedule_id: null, schedule: null, label: d.label, date: d.date, time: d.time, booking_id: d.booking_id, available: d.available,
      };
      out.push(e);
    });
    if (!text) continue;
    const dev = DEVANAGARI.test(text);
    const e: AgentEvent = { ...env(t.at), type: "agent", index: 0, text, audio_ms: null, readback, disclosure: /AI assistant/i.test(text), language: dev ? "hi" : lang, script: dev ? "devanagari" : "latin", words: tag(text) };
    out.push(e);
  }
  return out;
}
