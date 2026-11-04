"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import { api } from "@/lib/api";
import { clearToast, initialLive, reduceLive, type EventsResponse, type LiveState } from "@/lib/liveCall";

type Action = { type: "events"; events: EventsResponse["events"] } | { type: "reset" } | { type: "toast_done" };

function reducer(state: LiveState, a: Action): LiveState {
  if (a.type === "events") return reduceLive(state, a.events);
  if (a.type === "toast_done") return clearToast(state);
  return initialLive();
}

/**
 * Follows the live call events of one front desk by long polling (GET /api/agents/{id}/events?wait=): the answer
 * comes back the moment there is an event, so the screen trails the call by well under a second. It stops when the
 * tab is hidden or `enabled` is false, and starts again from where it left off.
 */
export function useLiveEvents(agentId: string | null | undefined, enabled = true) {
  const [state, dispatch] = useReducer(reducer, undefined, initialLive);
  const seq = useRef(0); // the newest event seen, read by the polling loop (not by rendering)

  useEffect(() => {
    seq.current = 0;
    dispatch({ type: "reset" });
    if (!agentId || !enabled) return;
    let alive = true;
    const ctl = new AbortController();
    (async () => {
      let first = true;
      while (alive) {
        const began = Date.now();
        try {
          // The first read answers at once (what already happened); the next ones wait for news.
          const res = await api.get<EventsResponse>(`/api/agents/${encodeURIComponent(agentId)}/events?after=${seq.current}&wait=${first ? 0 : 12}`, {
            signal: ctl.signal,
            retries: 0,
          });
          first = false;
          if (!alive) return;
          if (res.head < seq.current) {
            seq.current = 0;
            dispatch({ type: "reset" }); // the server restarted: its numbering began again
            continue;
          }
          if (res.events.length) dispatch({ type: "events", events: res.events });
          seq.current = Math.max(seq.current, res.last_seq);
          // An answer with nothing in it that came back at once (a server that does not hold the request) must not turn
          // this loop into a busy one.
          if (!res.events.length && Date.now() - began < 500) await new Promise((r) => setTimeout(r, 400));
        } catch {
          if (!alive) return;
          await new Promise((r) => setTimeout(r, 2000));
        }
        if (typeof document !== "undefined" && document.hidden) {
          await new Promise<void>((resolve) => {
            const on = () => document.hidden || (document.removeEventListener("visibilitychange", on), resolve());
            document.addEventListener("visibilitychange", on);
          });
        }
      }
    })();
    return () => {
      alive = false;
      ctl.abort();
    };
  }, [agentId, enabled]);

  const dismissToast = useCallback(() => dispatch({ type: "toast_done" }), []);
  const push = useCallback((events: EventsResponse["events"]) => dispatch({ type: "events", events }), []);
  return { state, dismissToast, push };
}
