"use client";

import { useId, useState } from "react";
import { CalendarClock } from "lucide-react";
import { Button } from "@/components/ui";
import { errMessage } from "@/components/desk/format";
import { moveBooking, type Booking } from "@/components/desk/calls";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/formatDate";
import { inputCls } from "./form/Fields";

interface Option {
  start: string;
  end?: string;
}
interface Refusal {
  message: string;
  options: Option[];
}

/** The date and the clock time of an ISO start written in the schedule's own time ("2026-11-05T17:00:00+05:30"). */
const dayOf = (iso: string) => iso.slice(0, 10);
const clockOf = (iso: string) => iso.slice(11, 16);

/** What the server says about a taken time: its sentence and the nearest free times either side. */
function refusal(e: unknown): Refusal | null {
  if (!(e instanceof ApiError) || e.code !== "SLOT_UNAVAILABLE") return null;
  const d = (e.details ?? {}) as { before?: Option | null; after?: Option | null; spoken?: { en?: string } };
  return { message: d.spoken?.en || e.message, options: [d.before, d.after].filter((o): o is Option => !!o?.start) };
}

/**
 * "Move" a booking: pick another day and time, the server checks that it is free (staff are not held to the minimum notice) and
 * keeps the booking's id. A taken time is explained with the nearest free times, one tap to use them.
 */
export function RescheduleForm({ booking, onMoved, onCancel }: { booking: Booking; onMoved: () => void; onCancel: () => void }) {
  const id = useId();
  const stay = !!booking.nights;
  const [day, setDay] = useState(booking.start ? dayOf(booking.start) : "");
  const [time, setTime] = useState(booking.start && !stay ? clockOf(booking.start) : "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [options, setOptions] = useState<Option[]>([]);

  const ready = !!day && (stay || !!time);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!booking.id || !ready) return;
    setBusy(true);
    setProblem(null);
    setOptions([]);
    try {
      await moveBooking(booking.id, `${day}T${stay ? "00:00" : time}`, null);
      onMoved();
    } catch (err) {
      const taken = refusal(err);
      if (taken) {
        setProblem(taken.message);
        setOptions(taken.options);
      } else {
        setProblem(errMessage(err, "Could not move the booking."));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} aria-label={`Move booking ${booking.id ?? ""}`} className="w-full space-y-3 rounded-xl border border-border bg-bg-base p-3.5">
      <p className="flex items-center gap-2 text-sm font-medium text-text-primary">
        <CalendarClock aria-hidden className="size-4 text-text-secondary" />
        Move to another {stay ? "check-in day" : "day and time"}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label htmlFor={`${id}-day`} className="space-y-1 text-[13px] text-text-secondary">
          <span>{stay ? "Check-in" : "Day"}</span>
          <input id={`${id}-day`} type="date" value={day} onChange={(e) => setDay(e.target.value)} className={`${inputCls} block w-auto`} required />
        </label>
        {!stay && (
          <label htmlFor={`${id}-time`} className="space-y-1 text-[13px] text-text-secondary">
            <span>Time</span>
            <input id={`${id}-time`} type="time" value={time} onChange={(e) => setTime(e.target.value)} className={`${inputCls} block w-auto`} required />
          </label>
        )}
      </div>
      {problem && (
        <div role="alert" className="space-y-2 text-sm">
          <p className="text-accent-error">{problem}</p>
          {options.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {options.map((o) => (
                <Button
                  key={o.start}
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setDay(dayOf(o.start));
                    if (!stay) setTime(clockOf(o.start));
                    setProblem(null);
                    setOptions([]);
                  }}
                >
                  Use {formatDateTime(o.start, booking.timezone)}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" loading={busy} disabled={!ready}>Move booking</Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>Keep it where it is</Button>
      </div>
    </form>
  );
}
