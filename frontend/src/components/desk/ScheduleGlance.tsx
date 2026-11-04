"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useNow } from "@/hooks/useNow";
import { accentColor, kindLine, nextFreeWords, nowIn, useSchedules } from "@/lib/schedules";

/**
 * Today's schedules at a glance: how many times are free, and the next free one, each linking to its calendar.
 * Renders nothing until there is at least one schedule, so a new desk is not nagged.
 */
export function ScheduleGlance() {
  const { data } = useSchedules(null);
  const now = useNow();
  if (!data?.length) return null;
  return (
    <section aria-labelledby="glance-title">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 id="glance-title" className="text-sm font-semibold text-text-primary">Your schedules</h2>
        <Link href="/desk/bookings" className="inline-flex min-h-8 items-center gap-1 text-sm font-medium text-accent-primary hover:underline">
          Open calendars <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {data.slice(0, 6).map((s) => {
          const today = nowIn(s.timezone, now).date;
          return (
            <li key={s.id}>
              <Link
                href={`/desk/bookings/${encodeURIComponent(s.id)}`}
                className="flex h-full flex-col gap-1 rounded-2xl border border-border bg-bg-surface p-4 transition-colors hover:border-border-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary"
              >
                <span className="flex items-center gap-2 text-[15px] font-semibold text-text-primary">
                  <span aria-hidden className="size-2.5 shrink-0 rounded-full" style={{ background: accentColor(s.accent) }} />
                  <span className="truncate">{s.name}</span>
                </span>
                <span className="text-sm text-text-secondary">{kindLine(s)}</span>
                <span className="mt-1 text-sm text-text-primary">
                  {s.summary ? `${s.summary.today_free} free today. Next free: ${nextFreeWords(s.summary.next_free, today)}` : "Loading"}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
