"use client";

import Link from "next/link";
import { ArrowRight, Phone } from "lucide-react";
import { ErrorBoundary } from "@/components/ui";
import { PageShell } from "@/components/ui/PageHeader";
import { BookingList } from "@/components/desk/BookingList";
import { NeedsAttention } from "@/components/desk/NeedsAttention";
import { TodayQueue } from "@/components/queue/TodayQueue";
import { SetupChecklist } from "@/components/agents/AgentHeader";
import { usePhoneConnected } from "@/components/agents/agentStatus";
import { isSameDay, isToday, madeToday, useBookings } from "@/components/desk/calls";
import { formatDate } from "@/lib/formatDate";
import { useAgents } from "@/hooks/useApi";
import { cn } from "@/lib/utils";
import { LiveCalls, useLiveCalls } from "./calls/LiveCalls";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** One sentence instead of a wall of tiles: what the front desk did today, in words. */
function todaySentence(calls: number, booked: number, missed: number): string {
  if (calls === 0) return "No calls yet today.";
  const tail = missed === 0 ? "None needed a person." : `${plural(missed, "call needs", "calls need")} a person.`;
  return `Vakya answered ${plural(calls, "call", "calls")} today and booked ${booked}. ${tail}`;
}

/** The quiet line shown while nobody is on the phone. */
function LineState() {
  const phone = usePhoneConnected();
  const live = useLiveCalls();
  if ((live.data?.calls.length ?? 0) > 0) return null;
  return (
    <p className="flex items-center gap-2 rounded-xl border border-border bg-bg-surface px-4 py-3 text-sm text-text-secondary">
      <span
        className={cn("size-2 shrink-0 rounded-full", phone ? "bg-accent-success" : "bg-border-3")}
        aria-hidden
      />
      {phone === undefined ? "Checking the phone line…" : phone ? "Line connected. Waiting for calls." : "Phone line not connected yet."}
    </p>
  );
}

/** Today answers three questions, top to bottom: what is happening now, what needs me, what is next. */
export default function DeskToday() {
  const bookings = useBookings(30);
  const agents = useAgents();
  const first = agents.data?.[0];
  const setupIncomplete = !!first && (first.document_count === 0 || !first.voice_id || first.system_instructions.trim() === "");

  const today = bookings.data
    .filter((b) => isToday(b) && b.status === "booked")
    .sort((a, b) => (a.start ?? "").localeCompare(b.start ?? ""));
  const callsToday = bookings.calls.filter((c) => isSameDay(c.when) && !c.practice);
  const missed = callsToday.filter((c) => c.outcome.key === "handed_off" || c.outcome.key === "couldnt_answer").length;
  const bookedOnCalls = bookings.data.filter((b) => madeToday(b)).length;

  return (
    <ErrorBoundary>
      <PageShell width="wide" className="space-y-7">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-accent-primary">{formatDate(new Date())}</p>
            <h1 className="font-display text-[clamp(1.75rem,2.6vw,2.5rem)] font-normal leading-[1.08] text-text-primary">Today</h1>
            <p className="mt-1.5 text-[15px] text-text-secondary">
              {todaySentence(callsToday.length, bookedOnCalls, missed)}{" "}
              <Link href="/desk/week" className="whitespace-nowrap font-medium text-accent-primary hover:underline">
                This week <ArrowRight className="inline size-3.5" aria-hidden />
              </Link>
            </p>
          </div>
          <Link
            href="/desk/try"
            className={cn(
              "inline-flex h-10 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-semibold text-[var(--color-on-accent)] transition-opacity hover:opacity-90",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary"
            )}
          >
            <Phone className="size-4" aria-hidden /> Try a call
          </Link>
        </header>

        <section aria-label="Now">
          <LiveCalls />
          <LineState />
        </section>

        <NeedsAttention />

        <section aria-labelledby="next-title" className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h2 id="next-title" className="text-sm font-semibold text-text-primary">
              Next up {today.length > 0 && <span className="ml-1 font-normal text-text-muted">{today.length}</span>}
            </h2>
            <Link href="/desk/bookings" className="inline-flex min-h-8 items-center gap-1 text-sm font-medium text-accent-primary hover:underline">
              All bookings <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          </div>
          <BookingList
            bookings={today}
            onChanged={() => void bookings.mutate()}
            emptyTitle="Nothing booked for today"
            emptyText="Appointments for today appear here in time order, with who booked them."
          />
          <TodayQueue />
        </section>

        {setupIncomplete && first && (
          <section aria-labelledby="setup-title" className="max-w-md">
            <h2 id="setup-title" className="mb-2 text-sm font-semibold text-text-primary">Finish setting up</h2>
            <SetupChecklist agent={first} />
          </section>
        )}
      </PageShell>
    </ErrorBoundary>
  );
}
