"use client";

import { Coffee, Plus, X } from "lucide-react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { fromMin, rangeWords, toMin, type Issue, type Range } from "@/lib/schedules";
import { TimeField } from "./TimeField";

/** Where a break goes when it is added: the middle of the last session, an hour long, on the hour. */
export function addBreak(ranges: Range[]): Range[] {
  if (!ranges.length) return [{ start: "09:00", end: "17:00" }];
  const last = ranges[ranges.length - 1];
  const s = toMin(last.start);
  const e = toMin(last.end);
  if (!Number.isNaN(s) && !Number.isNaN(e) && e - s >= 180) {
    const mid = Math.floor((s + e) / 2 / 60) * 60;
    const breakStart = Math.max(s + 60, Math.min(mid, e - 120));
    return [...ranges.slice(0, -1), { start: last.start, end: fromMin(breakStart) }, { start: fromMin(breakStart + 60), end: last.end }];
  }
  const from = Number.isNaN(e) ? 17 * 60 : Math.min(e + 60, 22 * 60);
  return [...ranges, { start: fromMin(from), end: fromMin(Math.min(from + 180, 1440)) }];
}

/** The opening sessions of a day: "Open from [9 am] to [1 pm]", the breaks between them, and "+ Add a break". */
export function RangesEditor({
  ranges,
  onChange,
  issues = [],
  label = "Open",
  compact = false,
}: {
  ranges: Range[];
  onChange: (next: Range[]) => void;
  issues?: Issue[];
  label?: string;
  compact?: boolean;
}) {
  const set = (i: number, patch: Partial<Range>) => onChange(ranges.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  return (
    <div className="space-y-2">
      {ranges.map((r, i) => {
        const bad = issues.some((x) => x.index === i);
        const prev = ranges[i - 1];
        return (
          <div key={i} className="space-y-2">
            {prev && toMin(r.start) > toMin(prev.end) && (
              <p className="flex items-center gap-1.5 pl-1 text-xs text-text-secondary">
                <Coffee aria-hidden className="size-3.5" /> Break, {rangeWords({ start: prev.end, end: r.start })}
              </p>
            )}
            <div className={cn("flex items-center gap-2", compact ? "flex-nowrap" : "flex-wrap")}>
              {!compact && <span className="w-12 text-sm text-text-secondary">{i === 0 ? label : "and"}</span>}
              <TimeField className={compact ? "w-[6.25rem] sm:w-[7.25rem]" : undefined} label={`${label} from, session ${i + 1}`} value={r.start} invalid={bad} onChange={(v) => set(i, { start: v })} />
              <span className="text-sm text-text-secondary">to</span>
              <TimeField className={compact ? "w-[6.25rem] sm:w-[7.25rem]" : undefined} label={`${label} until, session ${i + 1}`} value={r.end} after={r.start} invalid={bad} allowEndOfDay onChange={(v) => set(i, { end: v })} />
              {ranges.length > 1 && (
                <button
                  type="button"
                  aria-label={`Remove session ${i + 1}`}
                  onClick={() => onChange(ranges.filter((_, k) => k !== i))}
                  className="grid size-10 shrink-0 place-items-center rounded-lg text-text-secondary hover:bg-bg-base hover:text-text-primary"
                >
                  <X aria-hidden className="size-4" />
                </button>
              )}
            </div>
          </div>
        );
      })}
      {issues.map((x, i) => (
        <p key={i} role="alert" className="text-sm text-accent-error">{x.message}</p>
      ))}
      <Button type="button" variant="ghost" size="sm" onClick={() => onChange(addBreak(ranges))} leftIcon={<Plus className="size-3.5" />}>
        Add a break
      </Button>
    </div>
  );
}
