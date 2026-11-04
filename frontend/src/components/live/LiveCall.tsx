"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarDays, Check, MessageSquareText, X } from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { Button, Skeleton } from "@/components/ui";
import { BookingDrawer } from "@/components/schedules/BookingDrawer";
import { useScheduleBookings } from "@/components/desk/calls";
import { useMediaQuery } from "@/components/schedules/useMediaQuery";
import { formatDayTime } from "@/lib/formatDate";
import { shortName, useSchedules } from "@/lib/schedules";
import { cn } from "@/lib/utils";
import { CallLink } from "./CallLink";
import { Composer } from "./Composer";
import { LiveCalendar } from "./LiveCalendar";
import { Legend, LiveTranscript } from "./LiveTranscript";
import { useLiveEvents } from "./useLiveEvents";

/**
 * The live call, split in two: on the left what is being said (every word tinted by its script and language, the agent's
 * read-back marked while it is spoken, a chip for each thing it checks or does), on the right the front desk's schedule
 * reacting to it. A QR code above lets anyone call from a phone; on a phone this screen has two tabs (Call, Calendar) and
 * the booking toast shows on both.
 */
export function LiveCall({ agentId }: { agentId: string }) {
  const { state, dismissToast } = useLiveEvents(agentId);
  const schedules = useSchedules(agentId);
  const bookings = useScheduleBookings(null);
  const reduce = useReducedMotion();
  const wide = useMediaQuery("(min-width: 1024px)");
  const [tab, setTab] = useState<"call" | "calendar">("call");
  const [proof, setProof] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const conversation = useRef<string | null>(null); // the owner's own turns on this page share one conversation, across the tabs

  // A clock for "speaking" marks and the toast; it only runs while something is being said or shown.
  const active = state.speakingUntil > now || !!state.justBooked;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 150);
    return () => clearInterval(t);
  }, [active]);
  useEffect(() => {
    const t = setTimeout(() => setNow(Date.now()), 0);
    return () => clearTimeout(t);
  }, [state.lines.length]);

  const list = useMemo(() => schedules.data ?? [], [schedules.data]);
  const schedule = list.find((s) => s.id === state.focus.scheduleId) ?? list[0] ?? null;
  const toast = state.justBooked;

  // The booking is stored a moment after its event: reload the calendar and the list when it arrives.
  const bookedAt = toast?.at ?? 0;
  const { mutate: reloadBookings } = bookings;
  useEffect(() => {
    if (bookedAt) void reloadBookings();
  }, [bookedAt, reloadBookings]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(dismissToast, 14000);
    return () => clearTimeout(t);
  }, [toast, dismissToast]);

  const booking = proof ? (bookings.data.find((b) => b.id === proof) ?? null) : null;
  const refreshKey = state.focus.booked?.at ?? 0;
  const callOn = state.turnsEnded > 0 || state.lines.length > 0;

  const transcript = (
    <section aria-label="Live transcript" className="flex min-h-0 flex-col">
      <div className="min-h-0 flex-1">
        {state.lines.length === 0 ? (
          <div className="grid min-h-[16rem] place-items-center text-center">
            <div className="max-w-sm">
              <p className="font-display text-[1.75rem] leading-tight text-text-primary">Waiting for a call</p>
              <p className="mt-2 text-[15px] text-text-secondary">Scan the code with your phone and talk, or type something below. The words and what the front desk does about them appear here as they happen.</p>
            </div>
          </div>
        ) : (
          <LiveTranscript lines={state.lines} now={now} maxHeight={wide ? "min(60vh, 38rem)" : "56vh"} />
        )}
      </div>
      <div className="mt-4 space-y-3 border-t border-border pt-4">
        <Composer agentId={agentId} conversation={conversation} />
        <Legend />
      </div>
    </section>
  );

  const calendar = (
    <section aria-label="Schedule" className="min-w-0">
      {schedules.isLoading ? (
        <Skeleton className="h-[26rem] w-full rounded-2xl" />
      ) : schedule ? (
        <LiveCalendar schedule={schedule} focus={state.focus} refreshKey={refreshKey} />
      ) : (
        <div className="rounded-2xl border border-dashed border-border p-8 text-center text-text-secondary">This front desk has no schedule yet, so there is nothing to book into.</div>
      )}
    </section>
  );

  return (
    <div className="space-y-5" data-testid="live-call">
      <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-3 rounded-2xl border border-border bg-bg-surface p-3 sm:px-5 sm:py-3.5">
        <CallLink agentId={agentId} className="w-full sm:w-auto" />
        <AnimatePresence mode="wait" initial={false}>
          {toast ? (
            <motion.div
              key={toast.bookingId}
              role="status"
              aria-live="polite"
              data-testid="booking-toast"
              initial={reduce ? false : { opacity: 0, scale: 0.97 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
              className="flex w-full items-center gap-3.5 rounded-xl border border-[color-mix(in_srgb,var(--color-accent-success)_45%,transparent)] bg-success-tint px-4 py-3 sm:w-auto sm:min-w-[22rem]"
            >
              <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-full bg-bg-surface text-accent-success">
                <Check className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-display text-[1.375rem] leading-tight text-text-primary">Booked {toast.bookingId}</p>
                <p className="text-sm text-text-secondary sm:truncate">
                  {toast.name ? `${shortName(toast.name)} · ` : ""}
                  {formatDayTime(toast.date, toast.time)}
                </p>
              </div>
              {toast.evidenceId && (
                <Button size="sm" variant="outline" onClick={() => setProof(toast.bookingId)}>
                  See the proof
                </Button>
              )}
              <button type="button" onClick={dismissToast} aria-label="Dismiss" className="grid size-9 shrink-0 place-items-center rounded-lg text-text-secondary hover:bg-bg-surface">
                <X aria-hidden className="size-4" />
              </button>
            </motion.div>
          ) : (
            <motion.p key="status" exit={{ opacity: 0 }} className="flex items-center gap-2.5 text-[15px] text-text-secondary" role="status">
              <span aria-hidden className={cn("size-2.5 rounded-full", callOn ? "bg-accent-success" : "bg-border-3")} />
              {callOn ? "On a call" : "No call in progress"}
            </motion.p>
          )}
        </AnimatePresence>
      </div>

      {!wide && (
        <div role="tablist" aria-label="Call and calendar" className="grid grid-cols-2 gap-1.5 rounded-xl bg-bg-base p-1">
          {([["call", "Call", MessageSquareText], ["calendar", "Calendar", CalendarDays]] as const).map(([id, name, Icon]) => (
            <button
              key={id}
              role="tab"
              type="button"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={cn("flex min-h-11 items-center justify-center gap-2 rounded-lg text-[15px] font-medium", tab === id ? "bg-bg-surface text-text-primary shadow-sm" : "text-text-secondary")}
            >
              <Icon aria-hidden className="size-4" />
              {name}
            </button>
          ))}
        </div>
      )}

      <div className="relative">

        {wide ? (
          <div className="grid items-start gap-8 pt-2 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
            {transcript}
            {calendar}
          </div>
        ) : (
          <div className="pt-2" role="tabpanel" aria-label={tab === "call" ? "Call" : "Calendar"}>
            {tab === "call" ? transcript : calendar}
          </div>
        )}
      </div>

      {booking && <BookingDrawer booking={booking} onClose={() => setProof(null)} onChanged={() => void reloadBookings()} />}
    </div>
  );
}
