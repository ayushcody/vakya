"use client";

import { useState } from "react";
import { Clock } from "lucide-react";
import { Button } from "@/components/ui";
import { RunningLate } from "./RunningLate";
import { runningWords, type QueueToday } from "./queue";

/** A slim banner on a schedule's calendar while it is running late today: "Running 40 min late · reason · Change". */
export function LateBanner({ scheduleId, queue }: { scheduleId: string; queue: QueueToday | undefined }) {
  const [open, setOpen] = useState(false);
  if (!queue || queue.minutes_late <= 0) return null;
  return (
    <div role="status" className="rounded-xl border border-border bg-bg-surface px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <Clock aria-hidden className="size-4 shrink-0 text-accent-primary" />
        <span className="font-semibold text-text-primary">{runningWords(queue.minutes_late)}</span>
        {queue.reason && <span className="text-text-secondary">{queue.reason}</span>}
        <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="ml-auto">
          {open ? "Close" : "Change"}
        </Button>
      </div>
      {open && (
        <div className="mt-3 border-t border-border pt-3">
          <RunningLate scheduleId={scheduleId} queue={queue} onDone={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}
