"use client";

import { useCallback, useEffect, useState } from "react";
import { Phone } from "lucide-react";
import { Reveal, SplitHeading, useStill } from "./motion";

/**
 * An illustrative week on the landing page. Sample data, no backend calls. Visitors click or press Enter/Space on a
 * slot to open or close it; a moment after the section loads, a slot is "booked by phone" with a gentle pulse.
 */

export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;
export const HOURS = [9, 10, 11, 12, 13, 14, 15, 16] as const;
export type SlotState = "open" | "closed" | "booked" | "phone";

const key = (d: number, h: number) => `${d}-${h}`;
export const hourLabel = (h: number) => `${h > 12 ? h - 12 : h} ${h >= 12 ? "pm" : "am"}`;

/** Sample week: lunch closed, Saturday afternoon closed, a few slots already taken. */
export function initialSlots(): Record<string, SlotState> {
  const s: Record<string, SlotState> = {};
  DAYS.forEach((_, d) =>
    HOURS.forEach((h) => {
      s[key(d, h)] = h === 13 || (d === 5 && h >= 13) ? "closed" : "open";
    }),
  );
  for (const [d, h] of [[0, 10], [1, 14], [2, 9], [3, 15], [4, 11], [5, 10]]) s[key(d, h)] = "booked";
  return s;
}

/** Slots the phone call tries, in order; it takes the first one still open. */
const CALL_ORDER: [number, number][] = [[2, 11], [2, 12], [3, 10], [3, 11], [1, 10], [4, 10]];
const CALL_DELAY_MS = 2200;

const CELL: Record<SlotState, string> = {
  open: "bg-[var(--s-paper)] border-[var(--s-bar)] text-[var(--s-ink)] hover:border-[var(--violet-700)]",
  closed: "bg-[var(--s-fill)] border-[var(--s-line)] text-[var(--s-mute)]",
  booked: "bg-[var(--s-ink)] border-[var(--s-ink)] text-[var(--s-night-text)] cursor-default",
  phone: "bg-[var(--violet-700)] border-[var(--violet-700)] text-white cursor-default",
};

export function ScheduleVisualiser() {
  const still = useStill();
  const [slots, setSlots] = useState<Record<string, SlotState>>(initialSlots);
  const [call, setCall] = useState<{ d: number; h: number } | null>(null);
  const [ran, setRan] = useState(0);

  useEffect(() => {
    const t = window.setTimeout(() => {
      setSlots((prev) => {
        const pick = CALL_ORDER.find(([d, h]) => prev[key(d, h)] === "open");
        if (!pick) return prev;
        setCall({ d: pick[0], h: pick[1] });
        return { ...prev, [key(pick[0], pick[1])]: "phone" };
      });
    }, CALL_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [ran]);

  const toggle = useCallback((d: number, h: number) => {
    setSlots((prev) => {
      const cur = prev[key(d, h)];
      if (cur !== "open" && cur !== "closed") return prev;
      return { ...prev, [key(d, h)]: cur === "open" ? "closed" : "open" };
    });
  }, []);

  const replay = () => {
    setCall(null);
    setSlots((prev) => {
      const next = { ...prev };
      for (const k of Object.keys(next)) if (next[k] === "phone") next[k] = "open";
      return next;
    });
    setRan((n) => n + 1);
  };

  const labelFor = (d: number, h: number, st: SlotState) => {
    const base = `${DAYS[d]} ${hourLabel(h)}`;
    if (st === "open") return `${base}, open. Press to close.`;
    if (st === "closed") return `${base}, closed. Press to open.`;
    if (st === "phone") return `${base}, booked by phone.`;
    return `${base}, booked.`;
  };

  return (
    <section id="schedule" aria-labelledby="schedule-title" className="s-container mt-24 md:mt-32">
      <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-16">
        <div>
          <p className="s-eyebrow">Visual schedules</p>
          <SplitHeading id="schedule-title" text="Set your hours" accent="by sight." className="h2 mt-4 max-w-[14ch]" />
          <Reveal as="p" className="lede mt-5 max-w-[42ch]">
            Click a slot to open or close it. That is the whole setup: no forms. Watch the grid for a few seconds and a call books one.
          </Reveal>
          <p className="mt-4 text-sm text-[var(--s-mute)]">An illustration with sample data, not a live schedule.</p>
          <ul className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-sm text-[var(--s-ink)]" aria-label="Legend">
            {(["open", "closed", "booked", "phone"] as SlotState[]).map((st) => (
              <li key={st} className="flex items-center gap-2">
                <span aria-hidden className={`h-4 w-4 rounded border ${CELL[st].split(" ").filter((c) => !c.startsWith("hover") && !c.startsWith("cursor")).join(" ")}`} />
                {st === "open" ? "Open" : st === "closed" ? "Closed" : st === "booked" ? "Booked" : "Booked by phone"}
              </li>
            ))}
          </ul>
        </div>
        <Reveal scale>
          <div className="rounded-2xl border border-[var(--s-line)] bg-[var(--s-paper)] p-3 sm:p-5" style={{ boxShadow: "var(--s-shadow-paper)" }}>
            <div role="group" aria-label="Sample week, hourly slots" className="grid grid-cols-[2.5rem_repeat(6,minmax(0,1fr))] gap-1 sm:gap-1.5" data-testid="schedule-grid">
              <span aria-hidden />
              {DAYS.map((d) => (
                <span key={d} className="pb-1 text-center text-[0.7rem] font-semibold uppercase tracking-wide text-[var(--s-mute)] sm:text-xs">
                  {d}
                </span>
              ))}
              {HOURS.map((h) => (
                <Row key={h} h={h} slots={slots} still={still} toggle={toggle} labelFor={labelFor} />
              ))}
            </div>
            <div className="mt-4 flex min-h-[3rem] flex-wrap items-center justify-between gap-3 border-t border-[var(--s-line)] pt-3 text-sm" aria-live="polite">
              <p className="flex items-center gap-2 text-[var(--s-ink)]" data-testid="schedule-caption">
                <Phone className="h-4 w-4 shrink-0 text-[var(--violet-700)]" aria-hidden />
                {call ? `A caller just booked ${DAYS[call.d]} ${hourLabel(call.h)} by phone, after the read-back and a yes.` : "Waiting for a call…"}
              </p>
              {call && (
                <button type="button" onClick={replay} className="s-btn s-btn--secondary !px-3 !py-1.5 text-sm">
                  Replay
                </button>
              )}
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

function Row({ h, slots, still, toggle, labelFor }: { h: number; slots: Record<string, SlotState>; still: boolean; toggle: (d: number, h: number) => void; labelFor: (d: number, h: number, s: SlotState) => string }) {
  return (
    <>
      <span className="flex items-center justify-end pr-1 text-[0.7rem] tabular-nums text-[var(--s-mute)] sm:text-xs">{hourLabel(h)}</span>
      {DAYS.map((_, d) => {
        const st = slots[key(d, h)];
        const locked = st === "booked" || st === "phone";
        return (
          <button
            key={d}
            type="button"
            aria-label={labelFor(d, h, st)}
            aria-pressed={locked ? undefined : st === "open"}
            aria-disabled={locked || undefined}
            data-state={st}
            onClick={() => toggle(d, h)}
            className={`h-9 min-w-0 rounded-md border text-[0.65rem] font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--violet-700)] sm:h-10 ${still ? "" : "transition-colors duration-300"} ${CELL[st]} ${st === "phone" && !still ? "vk-slot-pulse" : ""}`}
          >
            <span aria-hidden>{st === "phone" ? "Phone" : st === "booked" ? "Booked" : ""}</span>
          </button>
        );
      })}
    </>
  );
}
