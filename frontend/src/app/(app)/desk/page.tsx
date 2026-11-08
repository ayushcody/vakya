"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { ArrowRight, Phone } from "lucide-react";
import { ErrorBoundary } from "@/components/ui";
import { PageShell } from "@/components/ui/PageHeader";
import { BookingList } from "@/components/desk/BookingList";
import { NeedsAttention } from "@/components/desk/NeedsAttention";
import { RecentCalls } from "@/components/desk/RecentCalls";
import { ScheduleGlance } from "@/components/desk/ScheduleGlance";
import { TodayQueue } from "@/components/queue/TodayQueue";
import { SetupChecklist } from "@/components/agents/AgentHeader";
import { UsageTiles } from "@/components/usage/UsageTiles";
import { LiveCalls } from "@/app/(app)/desk/calls/LiveCalls";
import { isSameDay, isToday, madeToday, useBookings } from "@/components/desk/calls";
import { useAgents } from "@/hooks/useApi";
import { useNow } from "@/hooks/useNow";
import { cn } from "@/lib/utils";

function greeting(hour: number): string {
  if (hour < 5) return "Hello";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

const subscribeNever = () => () => {};

function Tile({ label, value, hint, href }: { label: string; value: number | string; hint?: string; href?: string }) {
  const body = (
    <>
      <p className="text-xs font-semibold uppercase tracking-[1px] text-text-secondary">{label}</p>
      <p className="mt-2 font-display text-[1.875rem] leading-none text-text-primary tabular-nums sm:text-[2.25rem]">{value}</p>
      {hint && <p className="mt-1.5 text-sm text-text-secondary">{hint}</p>}
    </>
  );
  const cls = "block rounded-2xl border border-border bg-bg-surface p-4 sm:p-5";
  return href ? (
    <Link href={href} className={cn(cls, "transition-colors hover:border-border-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary")}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

/** Today: what the receptionist booked and how the calls went, what needs a person, and a way to try a call. */
export default function DeskToday() {
  // The server renders "Hello"; the browser swaps in the right greeting for its own clock without a mismatch.
  const hello = useSyncExternalStore(subscribeNever, () => greeting(new Date().getHours()), () => "Hello");
  const bookings = useBookings(30);
  const agents = useAgents();
  const now = useNow();
  const first = agents.data?.[0];
  const setupIncomplete = !!first && (first.document_count === 0 || !first.voice_id || first.system_instructions.trim() === "");

  const bookedToday = bookings.data.filter((b) => isToday(b));
  const madeTodayCount = bookings.data.filter((b) => madeToday(b)).length;
  const upcoming = bookings.data.filter((b) => b.status === "booked" && (b.start ? new Date(b.start).getTime() > now : false)).length;
  const callsToday = bookings.calls.filter((c) => isSameDay(c.when) && !c.practice).length;

  return (
    <ErrorBoundary>
      <PageShell width="wide" className="space-y-6">
        <header data-area="desk" className="flex flex-wrap items-end justify-between gap-4 border-l-2 border-[var(--area-accent)] pl-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-[var(--area-accent)]">{hello}</p>
            <h1 className="font-display text-[clamp(1.75rem,2.6vw,2.5rem)] font-normal leading-[1.08] tracking-[-0.005em] text-text-primary">Today</h1>
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

        <LiveCalls />

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Tile label="Booked today" value={bookedToday.length} hint="appointments happening today" href="/desk/bookings" />
          <Tile label="Made today" value={madeTodayCount} hint="appointments booked on calls today" />
          <Tile label="Calls today" value={callsToday} hint="not counting practice calls" />
          <Tile href="/desk/bookings" label="Upcoming" value={bookings.fromCalls ? bookings.data.length : upcoming} hint={bookings.fromCalls ? "appointments booked in the last 30 days" : "appointments still to come"} />
        </div>

        {/* What the speech costs is for the desk at the computer; on a phone, Today stays on bookings and calls. */}
        <div className="hidden gap-3 sm:grid sm:grid-cols-2 lg:grid-cols-4" aria-label="What the speech models cost">
          <UsageTiles />
        </div>

        <section aria-labelledby="today-bookings-title">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h2 id="today-bookings-title" className="text-sm font-semibold text-text-primary">Appointments today</h2>
            <Link href="/desk/bookings" className="inline-flex min-h-8 items-center gap-1 text-sm font-medium text-accent-primary hover:underline">
              All bookings <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          </div>
          <BookingList
            bookings={bookedToday}
            onChanged={() => void bookings.mutate()}
            emptyTitle="No appointments today"
            emptyText="Appointments that start today appear here, with the date and time the front desk read back."
          />
        </section>

        <TodayQueue />

        <ScheduleGlance />

        <NeedsAttention />
        <RecentCalls limit={5} />

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
