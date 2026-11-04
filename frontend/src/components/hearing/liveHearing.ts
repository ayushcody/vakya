import type { HearingEvent } from "./types";

/** The `hearing` events of a live call, by the turn they belong to (the caller's line of that turn carries the same turn id). */
export interface HearingState {
  byTurn: Record<string, HearingEvent[]>;
  order: string[];
  lastSeq: number;
}

export const initialHearing = (): HearingState => ({ byTurn: {}, order: [], lastSeq: 0 });

const MAX_TURNS = 60;

export function isHearingEvent(e: unknown): e is HearingEvent {
  return !!e && typeof e === "object" && (e as { type?: string }).type === "hearing" && typeof (e as { label?: unknown }).label === "string";
}

export function reduceHearing(state: HearingState, events: unknown[]): HearingState {
  let s = state;
  for (const e of events) {
    if (!isHearingEvent(e) || e.seq <= s.lastSeq || !e.turn_id) continue;
    const have = s.byTurn[e.turn_id] ?? [];
    const order = s.order.includes(e.turn_id) ? s.order : [...s.order, e.turn_id];
    const kept = order.slice(-MAX_TURNS);
    const byTurn: Record<string, HearingEvent[]> = {};
    for (const t of kept) byTurn[t] = t === e.turn_id ? [...have, e] : s.byTurn[t];
    s = { byTurn, order: kept, lastSeq: e.seq };
  }
  return s;
}
