"use client";

import { useEffect, useMemo, useRef } from "react";
import { useReducedMotion } from "motion/react";
import { CalendarClock } from "lucide-react";
import { NightsCalendar } from "@/components/schedules/NightsCalendar";
import { WeekCalendar } from "@/components/schedules/WeekCalendar";
import { useNow } from "@/hooks/useNow";
import { formatDayTime } from "@/lib/formatDate";
import type { CalendarFocus } from "@/lib/liveCall";
import { addDays, dayLabelLong, nowIn, useSlots, weekStart, monthGrid, type Schedule } from "@/lib/schedules";

const noop = () => {};

/** CSS for the slots the call is touching. Cells carry `data-start="2026-11-05T18:00:00+05:30"`, so a prefix finds them. */
export function focusCss(focus: CalendarFocus, scope = ".live-cal"): string {
  const rules: string[] = [];
  const cell = (date: string, time: string) => `${scope} [data-start^="${date}T${time}"]`;
  const d = focus.date;
  if (d && focus.checking) {
    const kind = focus.checking.state;
    rules.push(`${cell(d, focus.checking.time)}{--live:${kind === "free" ? "var(--color-accent-success)" : kind === "taken" ? "var(--color-orange-700)" : "var(--color-accent-primary)"};}`);
    rules.push(`${cell(d, focus.checking.time)}{box-shadow:0 0 0 3px var(--live)!important;z-index:6;}`);
    rules.push(`${cell(d, focus.checking.time)}::after{content:"${kind === "checking" ? "Checking" : kind === "free" ? "Free" : "Taken"}";}`);
    if (kind === "checking") rules.push(`${cell(d, focus.checking.time)}{animation:livePulse 1.3s ease-in-out infinite;}`);
  }
  if (focus.holding) {
    const { date, time } = focus.holding;
    rules.push(`${cell(date, time)}{--live:var(--color-accent-primary);background:color-mix(in srgb,var(--color-violet-600) 24%,var(--color-bg-surface))!important;box-shadow:0 0 0 3px var(--live)!important;z-index:6;}`);
    rules.push(`${cell(date, time)}::after{content:"Read back, waiting for a yes";}`);
  }
  for (const o of focus.offered) {
    if (!o.time) continue;
    rules.push(`${cell(o.date, o.time)}{--live:var(--color-accent-primary);box-shadow:0 0 0 2px var(--live)!important;background:color-mix(in srgb,var(--color-violet-600) 10%,var(--color-bg-surface))!important;z-index:5;}`);
    rules.push(`${cell(o.date, o.time)}::after{content:"Offered";}`);
  }
  if (focus.booked) {
    const { date, time } = focus.booked;
    rules.push(`${cell(date, time)}{--live:var(--color-accent-success);box-shadow:0 0 0 3px var(--live)!important;z-index:7;background-image:linear-gradient(var(--color-success-tint),var(--color-success-tint))!important;background-repeat:no-repeat;background-size:100% 100%;animation:liveFill 1.1s cubic-bezier(.16,1,.3,1) both,liveRing 1.6s ease-out 1;}`);
    rules.push(`${cell(date, time)}::after{content:"Booked \\2713";color:var(--color-accent-success);}`);
  }
  return rules.join("\n");
}

/**
 * Scroll the calendar's own scroll box so the slot that starts at `key` ("2026-11-05T18:00") is in the middle of it. Looks for
 * the nearest scrollable ancestor of the cell inside `root`; the page itself is never scrolled. Returns whether the cell exists.
 */
export function scrollSlotIntoView(root: HTMLElement | null, key: string, smooth: boolean): boolean {
  const cell = root?.querySelector<HTMLElement>(`[data-start^="${key}"]`);
  if (!root || !cell) return false;
  let box: HTMLElement | null = cell.parentElement;
  while (box && root.contains(box)) {
    const overflow = typeof getComputedStyle === "function" ? getComputedStyle(box).overflowY : "";
    if ((overflow === "auto" || overflow === "scroll") && box.scrollHeight > box.clientHeight) break;
    box = box.parentElement;
  }
  if (!box || !root.contains(box) || !box.scrollTo) return true;       // nothing to scroll (a month grid shows everything)
  const top = cell.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - box.clientHeight / 2 + cell.clientHeight / 2;
  box.scrollTo({ top: Math.max(0, top), behavior: smooth ? "smooth" : "auto" });
  return true;
}

/**
 * The front desk's schedule for the day of the call, read only. The slot being checked pulses, the nearest free times it
 * offers say "Offered", a time read back waits for the yes, and when the caller says yes the slot fills in calmly and says
 * "Booked". It is the bookings calendar (components/schedules) with these states laid over it.
 */
export function LiveCalendar({ schedule, focus, refreshKey }: { schedule: Schedule; focus: CalendarFocus; refreshKey: number }) {
  const now = useNow();
  const today = now ? nowIn(schedule.timezone, now).date : "";
  const date = focus.date ?? today;
  const nights = schedule.kind === "nights";
  const range = useMemo(() => {
    if (!date) return { from: "", to: "" };
    if (nights) {
      const g = monthGrid(date);
      return { from: g[0], to: g[41] };
    }
    return { from: weekStart(date), to: addDays(weekStart(date), 6) };
  }, [date, nights]);
  const slots = useSlots(date ? schedule.id : null, range.from, range.to);
  const { mutate } = slots;
  useEffect(() => {
    if (refreshKey) void mutate();
  }, [refreshKey, mutate]);

  const css = useMemo(() => focusCss(focus), [focus]);

  // Bring the slot the call is about into view inside the calendar's own scroll box (never scrolling the page): the slot being
  // checked, the one read back, and above all the one just booked. Tried again when the slots load, because a booked slot's
  // cell may only exist once the calendar has been reloaded.
  const root = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const target = focus.booked ?? focus.holding ?? (focus.checking ? { date, time: focus.checking.time } : null) ?? focus.offered[0] ?? null;
  const targetKey = target && target.time ? `${target.date}T${target.time}` : "";
  const targetAt = focus.booked?.at ?? 0;
  useEffect(() => {
    if (!targetKey) return;
    if (scrollSlotIntoView(root.current, targetKey, !reduce)) return;
    const raf = requestAnimationFrame(() => scrollSlotIntoView(root.current, targetKey, !reduce));
    return () => cancelAnimationFrame(raf);
  }, [targetKey, targetAt, slots.data, reduce]);
  const elsewhere = focus.offered.filter((o) => o.date !== date && o.time);

  return (
    <div ref={root} className="live-cal min-w-0">
      <style>{`
        @keyframes livePulse { 0%,100% { box-shadow: 0 0 0 2px var(--live); } 50% { box-shadow: 0 0 0 7px color-mix(in srgb, var(--live) 22%, transparent); } }
        @keyframes liveFill { from { background-size: 0% 100%; } to { background-size: 100% 100%; } }
        @keyframes liveRing { from { box-shadow: 0 0 0 3px var(--live), 0 0 0 3px color-mix(in srgb, var(--live) 45%, transparent); } to { box-shadow: 0 0 0 3px var(--live), 0 0 0 22px transparent; } }
        .live-cal [role=gridcell] button { font-size: 15px; }
        .live-cal [data-start] { position: relative; }
        .live-cal [data-start]::after { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); font-size: 13px; font-weight: 600; letter-spacing: .2px; color: var(--live, inherit); pointer-events: none; }
        @media (prefers-reduced-motion: reduce) { .live-cal [data-start] { animation: none !important; } }
        ${css}
      `}</style>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="font-display text-[1.75rem] leading-tight text-text-primary">{date ? dayLabelLong(date) : "Schedule"}</p>
          <p className="text-sm text-text-secondary">{schedule.name}</p>
        </div>
        {elsewhere.length > 0 && (
          <p className="flex flex-wrap items-center gap-2 text-sm text-text-secondary" role="status">
            <CalendarClock aria-hidden className="size-4" /> Also offered:
            {elsewhere.map((o) => (
              <span key={`${o.date}${o.time}`} className="rounded-full bg-violet-200 px-2.5 py-0.5 font-medium text-violet-700 [[data-theme=dark]_&]:bg-accent-primary/15 [[data-theme=dark]_&]:text-accent-secondary">
                {formatDayTime(o.date, o.time)}
              </span>
            ))}
          </p>
        )}
      </div>
      {nights ? (
        <NightsCalendar schedule={schedule} month={date || today || "2026-01-01"} data={slots.data} loading={slots.isLoading} nowMs={now} highlight={[]} onOpenSlot={noop} />
      ) : (
        <WeekCalendar schedule={schedule} days={date ? [date] : []} data={slots.data} loading={slots.isLoading} nowMs={now} highlight={[]} compact onOpenSlot={noop} onBlockRange={noop} />
      )}
    </div>
  );
}
