"use client";

import { useEffect, useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import {
  DAY_SHORT,
  DAY_KEYS,
  accentColor,
  dayLabelLong,
  isoDate,
  monthGrid,
  guestLine,
  nowIn,
  slotLabel,
  type CellState,
  type Schedule,
  type Slot,
  type SlotsResponse,
} from "@/lib/schedules";
import { cellStyle } from "./WeekCalendar";
import { SlotHoverCard, useSlotHover } from "./SlotHoverCard";

const interactive = (s: CellState) => s === "free" || s === "booked" || s === "full" || s === "blocked";

/** A month of nights: rooms left under each date; click a night to block it or book someone. */
export function NightsCalendar({
  schedule,
  month,
  data,
  loading,
  nowMs,
  highlight,
  onOpenSlot,
}: {
  schedule: Schedule;
  /** Any date in the month to show. */
  month: string;
  data: SlotsResponse | undefined;
  loading: boolean;
  nowMs: number;
  highlight: string[];
  onOpenSlot: (slot: Slot, el: HTMLElement) => void;
}) {
  const cells = useMemo(() => monthGrid(month), [month]);
  const byDate = useMemo(() => new Map((data?.days ?? []).map((d) => [d.date, d])), [data]);
  const today = nowIn(schedule.timezone, nowMs).date;
  const [focus, setFocus] = useState<string | null>(null);
  const hover = useSlotHover();
  const hl = new Set(highlight.map(isoDate));
  const stateOf = (date: string): { slot?: Slot; state: CellState } => {
    const slot = byDate.get(date)?.slots[0];
    return { slot, state: slot?.status ?? "closed" };
  };
  const firstOpen = cells.find((d) => interactive(stateOf(d).state));
  const tab = focus && interactive(stateOf(focus).state) ? focus : firstOpen;

  useEffect(() => {
    const first = highlight[0];
    if (!first) return;
    document.querySelector<HTMLElement>(`[data-night="${isoDate(first)}"]`)?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [highlight]);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const t = (e.target as HTMLElement).closest<HTMLElement>("[data-night]");
    if (!t) return;
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : e.key === "ArrowDown" ? 7 : e.key === "ArrowUp" ? -7 : 0;
    if (!step) return;
    e.preventDefault();
    let i = cells.indexOf(t.dataset.night!);
    for (;;) {
      i += step;
      if (i < 0 || i >= cells.length) return;
      if (interactive(stateOf(cells[i]).state)) break;
    }
    setFocus(cells[i]);
    e.currentTarget.querySelector<HTMLElement>(`[data-night="${cells[i]}"]`)?.focus();
  }

  const vars = { "--cell-accent": accentColor(schedule.accent), "--cell-tint": `color-mix(in srgb, ${accentColor(schedule.accent)} 12%, var(--color-bg-surface))` } as CSSProperties;
  const monthKey = month.slice(0, 7);
  return (
    <div role="grid" aria-label={`${schedule.name} nights`} aria-busy={loading} onKeyDown={onKeyDown} style={vars} className="overflow-hidden rounded-2xl border border-border bg-bg-surface">
      <SlotHoverCard target={hover.target} />
      <div role="row" className="grid grid-cols-7 border-b border-border">
        {DAY_KEYS.map((d) => (
          <div key={d} role="columnheader" className="py-2 text-center text-xs font-medium text-text-secondary">
            {DAY_SHORT[d]}
          </div>
        ))}
      </div>
      {Array.from({ length: 6 }, (_, w) => (
        <div key={w} role="row" className="grid grid-cols-7">
          {cells.slice(w * 7, w * 7 + 7).map((date, i) => {
            const { slot, state } = stateOf(date);
            const look = cellStyle(state, schedule.accent, slot && state === "booked" ? 1 - slot.remaining / schedule.capacity : 0);
            const inMonth = date.startsWith(monthKey);
            const isToday = date === today;
            const guests = slot && (state === "booked" || state === "full") ? slot.bookings : [];
            const label = slot ? (state === "free" ? `${slot.remaining} left` : guests.length ? "" : slotLabel(slot, schedule.capacity)) : "";
            const aria = `${dayLabelLong(date)}, ${slot ? (state === "free" ? `${slot.remaining} rooms left` : state === "booked" ? `${slot.remaining} of ${schedule.capacity} rooms left` : state === "full" ? "no rooms left" : state === "blocked" ? "busy" : "in the past") : "closed"}`;
            const inner = (
              <>
                <span className={cn("text-sm font-semibold tabular-nums", isToday && "grid size-6 place-items-center rounded-full ring-2 ring-[var(--cell-accent)]")}>
                  {Number(date.slice(8))}
                </span>
                {guests.length > 0 ? (
                  <span className="mt-auto w-full min-w-0 text-[11px] leading-tight">
                    <span className="block truncate font-medium">{guestLine(guests[0])}</span>
                    {guests.length === 2 && <span className="block truncate">{guestLine(guests[1])}</span>}
                    {guests.length > 2 && <span className="block truncate text-text-secondary">+{guests.length - 1} more</span>}
                    {state === "booked" && <span className="block truncate text-text-secondary">{slot!.remaining} left</span>}
                  </span>
                ) : (
                  <span className="mt-auto truncate text-[11px] leading-tight">{label}</span>
                )}
              </>
            );
            const base = cn(
              "flex h-[4.25rem] w-full flex-col items-start border-b border-border p-1.5 text-left sm:h-20 sm:p-2",
              i > 0 && "border-l",
              !inMonth && "opacity-50"
            );
            return slot && interactive(state) ? (
              <button
                key={date}
                type="button"
                role="gridcell"
                data-night={date}
                data-start={slot.start}
                data-status={state}
                tabIndex={tab === date ? 0 : -1}
                aria-label={aria}
                {...hover.bind(slot)}
                onFocus={(e) => {
                  setFocus(date);
                  hover.bind(slot).onFocus(e);
                }}
                onClick={(e) => onOpenSlot(slot, e.currentTarget)}
                className={cn(
                  base,
                  "text-text-primary transition-colors focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent-primary",
                  look.className,
                  hl.has(date) && "ring-2 ring-inset ring-accent-primary"
                )}
                style={look.style}
              >
                {inner}
              </button>
            ) : (
              <div key={date} role="gridcell" aria-label={aria} className={cn(base, look.className, "text-text-secondary")}>
                {inner}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
