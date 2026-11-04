import type { TheatreRun, TheatreRunRow, TheatreScenario, TheatreToolCall } from "./types";
import { API_URL } from "@/lib/apiBase";

/** "0.7 s", "12 s", or "n/a" for a missing number. */
export function seconds(ms: number | null | undefined): string {
  if (ms == null) return "n/a";
  const s = ms / 1000;
  return s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`;
}

export function percent(v: number | null | undefined): string {
  return v == null ? "n/a" : `${Math.round(v)}%`;
}

/** The line a scenario's caller is on, in words. `planned` marks a text run, where the line was not applied. */
export function lineLabel(s: TheatreScenario, input: TheatreRun["input"]): string {
  const codec = s.channel.codec === "g711" ? "8 kHz G.711" : "8 kHz phone band";
  const noise = s.channel.snr_db == null ? "no added noise" : `${s.channel.snr_db} dB noise`;
  return `${codec}, ${noise}${input === "text" ? " (planned)" : ""}`;
}

export function audioUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  const rel = path.replace(/^audio\//, "");
  return `${API_URL}/api/theatre/audio/${rel.split("/").map(encodeURIComponent).join("/")}`;
}

const TOOL_WORDS: Record<string, string> = {
  check_availability: "checked availability",
  list_free_slots: "listed free times",
  book: "booked",
  cancel: "cancelled",
  find_booking: "looked up bookings by phone",
};

/** A chip's words: what the tool did, and how it ended. A refused `book` is the read-back step, not an error. */
export function toolChip(c: TheatreToolCall): { text: string; tone: "ok" | "note" | "bad"; raw: string } {
  const name = c.name.split(".").pop() ?? c.name;
  const raw = c.result ?? "";
  if (!c.ok && raw.includes("NOT DONE")) {
    return { text: name === "cancel" ? "asked before cancelling" : "read it back before booking", tone: "note", raw: name };
  }
  if (!c.ok) return { text: `${TOOL_WORDS[name] ?? name} failed`, tone: "bad", raw: name };
  return { text: TOOL_WORDS[name] ?? name, tone: "ok", raw: name };
}

/** The newest run of each model mode and input, for the switcher. */
export function newestPerKind(runs: TheatreRunRow[]): TheatreRunRow[] {
  const seen = new Set<string>();
  const out: TheatreRunRow[] = [];
  for (const r of runs) {
    const k = `${r.mode}-${r.input}`;
    if (!seen.has(k)) {
      seen.add(k);
      out.push(r);
    }
  }
  return out;
}

export const MODE_NAME: Record<TheatreRun["mode"], string> = { scripted: "Scripted model", live: "Live model" };
export const INPUT_NAME: Record<TheatreRun["input"], string> = { audio: "Audio run", text: "Text run" };
