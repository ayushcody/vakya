"use client";

import { cn } from "@/lib/utils";
import {
  DAY_KEYS,
  DAY_SHORT,
  accentColor,
  draftWeek,
  hoursInWords,
  scheduleSentence,
  toMin,
  type Issue,
  type ScheduleDraft,
  type Week,
} from "@/lib/schedules";

/** The week as seven columns with the open hours drawn in. */
export function MiniWeek({ week, accent }: { week: Week; accent?: string | null }) {
  const all = DAY_KEYS.flatMap((d) => week[d]).map((r) => [toMin(r.start), toMin(r.end)] as const).filter(([a, b]) => !Number.isNaN(a) && !Number.isNaN(b) && b > a);
  const lo = all.length ? Math.max(0, Math.floor(Math.min(...all.map((x) => x[0])) / 60) * 60 - 60) : 480;
  const hi = all.length ? Math.min(1440, Math.ceil(Math.max(...all.map((x) => x[1])) / 60) * 60 + 60) : 1080;
  const span = Math.max(240, hi - lo);
  const color = accentColor(accent);
  return (
    <div role="img" aria-label={`Week preview. ${hoursInWords(week, "long")}`} className="grid grid-cols-7 gap-1.5">
      {DAY_KEYS.map((d) => {
        const closed = week[d].length === 0;
        return (
          <div key={d} className="flex flex-col items-center gap-1.5">
            <div className={cn("relative h-28 w-full overflow-hidden rounded-md border border-border", closed ? "bg-bg-base" : "bg-bg-surface")}>
              {week[d].map((r, i) => {
                const a = toMin(r.start);
                const b = toMin(r.end);
                if (Number.isNaN(a) || Number.isNaN(b) || b <= a) return null;
                return (
                  <span
                    key={i}
                    className="absolute inset-x-0.5 rounded-[4px]"
                    style={{
                      top: `${((a - lo) / span) * 100}%`,
                      height: `${Math.max(3, ((b - a) / span) * 100)}%`,
                      background: `color-mix(in srgb, ${color} 55%, var(--color-bg-surface))`,
                    }}
                  />
                );
              })}
              {closed && <span className="absolute inset-0 grid place-items-center text-[10px] text-text-secondary">Closed</span>}
            </div>
            <span className="text-[11px] font-medium text-text-secondary">{DAY_SHORT[d]}</span>
          </div>
        );
      })}
    </div>
  );
}

/** What callers will hear, in one sentence, with the week under it. Updates as the form changes. */
export function SchedulePreview({ draft, issues, className }: { draft: ScheduleDraft; issues: Issue[]; className?: string }) {
  const hasIssues = issues.some((i) => i.field !== "name");
  return (
    <section aria-label="Live preview" className={cn("rounded-2xl border border-border bg-bg-surface p-5", className)}>
      <h2 className="text-sm font-semibold text-text-primary">What callers will hear</h2>
      <p className="mt-2 font-display text-[1.375rem] leading-snug text-text-primary" aria-live="polite">
        {draft.name.trim() ? <span className="text-text-secondary">{draft.name.trim()}. </span> : null}
        {hasIssues ? "Finish the highlighted parts to see the full sentence." : scheduleSentence(draft)}
      </p>
      <div className="mt-5">
        <MiniWeek week={draftWeek(draft)} accent={draft.accent} />
      </div>
      {draft.closed_dates.length > 0 && (
        <p className="mt-4 text-xs text-text-secondary">
          Closed on {draft.closed_dates.length} holiday{draft.closed_dates.length === 1 ? "" : "s"}.
        </p>
      )}
    </section>
  );
}
