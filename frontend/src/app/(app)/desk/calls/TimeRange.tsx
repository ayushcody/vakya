"use client";

import { useId } from "react";
import { CalendarDays } from "lucide-react";
import { cn } from "@/lib/utils";
import { RANGE_KEYS, RANGE_LABEL, dayWords, rangeDays, type RangeKey, type TimeRange } from "./format";

/** Today, Yesterday, Last 7 days, Last 30 days or Custom (two dates). The page keeps the choice in the address. */
export function TimeRangeBar({ value, today, onChange, summary }: { value: TimeRange; today: string; onChange: (r: TimeRange) => void; summary?: string }) {
  const id = useId();
  const custom = value.kind === "custom";
  const days = rangeDays(value, today);
  const seg = (active: boolean) =>
    cn(
      "inline-flex h-8 items-center whitespace-nowrap rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary",
      active ? "bg-bg-surface text-text-primary shadow-[0_1px_2px_rgba(0,0,0,0.08)]" : "text-text-secondary hover:text-text-primary"
    );
  const bad = custom && !!value.from && !!value.to && value.from > value.to;
  const dateCls = "h-9 rounded-lg border border-border bg-bg-surface px-2.5 text-sm text-text-primary tabular-nums";
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2.5">
      <div role="group" aria-label="Time range" className="flex max-w-full overflow-x-auto rounded-lg bg-bg-base p-1 ring-1 ring-inset ring-border [scrollbar-width:none]">
        {RANGE_KEYS.map((k: RangeKey) => (
          <button key={k} type="button" aria-pressed={!custom && value.kind === "preset" && value.key === k} onClick={() => onChange({ kind: "preset", key: k })} className={seg(!custom && value.kind === "preset" && value.key === k)}>
            {RANGE_LABEL[k]}
          </button>
        ))}
        <button type="button" aria-pressed={custom} onClick={() => onChange({ kind: "custom", from: days.from, to: days.to })} className={cn(seg(custom), "gap-1.5")}>
          <CalendarDays aria-hidden className="size-3.5" />Custom
        </button>
      </div>
      {custom && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-text-secondary">
          <label htmlFor={`${id}-from`}>From</label>
          <input id={`${id}-from`} type="date" value={value.from} max={value.to || today} onChange={(e) => onChange({ kind: "custom", from: e.target.value, to: value.to })} className={dateCls} aria-invalid={bad || undefined} />
          <label htmlFor={`${id}-to`}>to</label>
          <input id={`${id}-to`} type="date" value={value.to} min={value.from || undefined} max={today} onChange={(e) => onChange({ kind: "custom", from: value.from, to: e.target.value })} className={dateCls} aria-invalid={bad || undefined} />
          {bad && <span role="alert" className="text-accent-error">The first day is after the last.</span>}
        </div>
      )}
      {!bad && (
        <p className="text-sm text-text-secondary" aria-live="polite">
          {days.from === days.to ? dayWords(days.from, today.slice(0, 4)) : `${dayWords(days.from, today.slice(0, 4))} to ${dayWords(days.to, today.slice(0, 4))}`}
          {summary ? ` · ${summary}` : ""}
        </p>
      )}
    </div>
  );
}
