"use client";

import { Clock } from "lucide-react";
import { Skeleton } from "@/components/ui";
import { useSchedules, type ScheduleWithSummary } from "@/lib/schedules";
import { RunningLate } from "./RunningLate";
import { TokenBoard } from "./TokenBoard";
import { runningWords, useToday } from "./queue";

function Panel({ schedule }: { schedule: ScheduleWithSummary }) {
  const { data, isLoading } = useToday(schedule.id);
  return (
    <section aria-labelledby={`queue-${schedule.id}`} className="space-y-4 rounded-2xl border border-border bg-bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`queue-${schedule.id}`} className="text-[15px] font-semibold text-text-primary">{schedule.name}</h3>
        {data && data.minutes_late > 0 && (
          <p className="inline-flex items-center gap-1.5 text-sm font-medium text-text-primary">
            <Clock aria-hidden className="size-4 text-accent-primary" />
            {runningWords(data.minutes_late)}
            {data.reason ? <span className="font-normal text-text-secondary"> · {data.reason}</span> : null}
          </p>
        )}
      </div>
      <RunningLate scheduleId={schedule.id} queue={data} />
      {isLoading && !data ? <Skeleton className="h-24 w-full rounded-xl" /> : data ? <TokenBoard scheduleId={schedule.id} queue={data} /> : null}
    </section>
  );
}

/**
 * The Today page's queue: for each time-slot schedule, a "running late" control and the live token board. Renders nothing
 * until there is a time-slot schedule (stays have no queue).
 */
export function TodayQueue() {
  const { data } = useSchedules(null);
  const slots = (data ?? []).filter((s) => s.kind === "slots").slice(0, 4);
  if (!slots.length) return null;
  return (
    <section aria-labelledby="queue-title" className="space-y-3">
      <div>
        <h2 id="queue-title" className="text-sm font-semibold text-text-primary">Queue and running late</h2>
        <p className="mt-0.5 text-sm text-text-secondary">When the doctor is late, say so once. Callers who ask &quot;how many before me?&quot; are told their token and about when they will be seen.</p>
      </div>
      <div className="space-y-3">
        {slots.map((s) => (
          <Panel key={s.id} schedule={s} />
        ))}
      </div>
    </section>
  );
}
