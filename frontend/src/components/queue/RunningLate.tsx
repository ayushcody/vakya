"use client";

import { useId, useState } from "react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { Button } from "@/components/ui";
import { chipClass } from "@/components/schedules/form/chipStyle";
import { plainError } from "@/lib/schedules";
import { cn } from "@/lib/utils";
import { DELAY_CHOICES, MAX_LATE, REASON_MAX, lateWords, setDelay, todayKey, type QueueToday } from "./queue";

/**
 * "Running late" for today: one tap on a chip (On time, 15, 30, 45, 60 minutes, or your own number) and an optional reason.
 * The phone agent tells callers who ask, and the list below shows who is moved. Tapping a chip uses the reason as typed.
 */
export function RunningLate({ scheduleId, queue, onDone }: { scheduleId: string; queue: QueueToday | undefined; onDone?: () => void }) {
  const { mutate } = useSWRConfig();
  const late = queue?.minutes_late ?? 0;
  const [reason, setReason] = useState<string | null>(null);
  const [other, setOther] = useState(false);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const reasonValue = reason ?? queue?.reason ?? "";
  const reasonId = useId();
  const customId = useId();
  const isPreset = (DELAY_CHOICES as readonly number[]).includes(late);

  async function apply(minutes: number) {
    if (busy) return;
    setBusy(true);
    try {
      const next = await setDelay(scheduleId, minutes, minutes === 0 ? "" : reasonValue);
      await mutate(todayKey(scheduleId), next, { revalidate: false });
      setReason(null);
      setOther(false);
      toast.success(minutes === 0 ? "Back on time. Callers will not hear about a delay." : `Marked ${lateWords(minutes)}. Callers who ask will be told.`);
      onDone?.();
    } catch (e) {
      toast.error(plainError(e, "Could not change that. Nothing was changed."));
    } finally {
      setBusy(false);
    }
  }

  const customMinutes = Number(custom);
  const customOk = Number.isInteger(customMinutes) && customMinutes >= 1 && customMinutes <= MAX_LATE;

  return (
    <div className="space-y-3">
      <div role="group" aria-label="How late are you running today" className="flex flex-wrap gap-2">
        {DELAY_CHOICES.map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={late === m && !other}
            disabled={busy}
            onClick={() => void apply(m)}
            className={chipClass(late === m && !other, "min-h-10 rounded-lg px-3.5 text-sm disabled:opacity-60")}
          >
            {m === 0 ? "On time" : `${m} min`}
          </button>
        ))}
        <button
          type="button"
          aria-pressed={other || (late > 0 && !isPreset)}
          aria-expanded={other}
          onClick={() => setOther((v) => !v)}
          className={chipClass(other || (late > 0 && !isPreset), "min-h-10 rounded-lg px-3.5 text-sm")}
        >
          {late > 0 && !isPreset && !other ? lateWords(late) : "Other"}
        </button>
      </div>

      {other && (
        <div className="flex flex-wrap items-end gap-2">
          <label htmlFor={customId} className="text-sm text-text-secondary">
            Minutes late
            <input
              id={customId}
              type="number"
              inputMode="numeric"
              min={1}
              max={MAX_LATE}
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              className="mt-1 block h-10 w-28 rounded-lg border border-border bg-bg-surface px-3 text-sm text-text-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
            />
          </label>
          <Button size="md" disabled={!customOk || busy} disabledReason={`Enter a whole number from 1 to ${MAX_LATE}`} onClick={() => void apply(customMinutes)}>
            Set
          </Button>
        </div>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <label htmlFor={reasonId} className="min-w-0 flex-1 basis-56 text-sm text-text-secondary">
          Reason (optional, callers may be told)
          <input
            id={reasonId}
            type="text"
            maxLength={REASON_MAX}
            value={reasonValue}
            onChange={(e) => setReason(e.target.value)}
            placeholder="for example, an emergency surgery"
            className={cn(
              "mt-1 block h-10 w-full rounded-lg border border-border bg-bg-surface px-3 text-sm text-text-primary placeholder:text-text-secondary/70",
              "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
            )}
          />
        </label>
        {late > 0 && reasonValue !== (queue?.reason ?? "") && (
          <Button size="md" variant="outline" disabled={busy} onClick={() => void apply(late)}>
            Update reason
          </Button>
        )}
      </div>
    </div>
  );
}
