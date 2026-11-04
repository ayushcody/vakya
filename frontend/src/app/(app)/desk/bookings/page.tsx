"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { CalendarCheck, CalendarPlus, Plus, X } from "lucide-react";
import { EmptyState, ErrorBoundary, ErrorState, Skeleton } from "@/components/ui";
import { PageShell } from "@/components/ui/PageHeader";
import { BookingExplorer } from "@/components/desk/BookingExplorer";
import { useScheduleBookings } from "@/components/desk/calls";
import { ALL_DESKS, FrontDeskSelect } from "@/components/schedules/FrontDeskSelect";
import { BookingDrawer } from "@/components/schedules/BookingDrawer";
import { LinkButton } from "@/components/schedules/LinkButton";
import { NewScheduleCard, ScheduleCard } from "@/components/schedules/ScheduleCard";
import { useAgents } from "@/hooks/useApi";
import { useNow } from "@/hooks/useNow";
import { plainError, readPref, useSchedules, writePref } from "@/lib/schedules";

/** Takes ?booking= out of the address once its drawer is closed, so a reload does not open it again. */
function clearBookingParam() {
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete("booking");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}`);
  } catch {
    /* a browser without history access keeps the address as it is */
  }
}

function BookingsContent() {
  const agents = useAgents();
  const linked = useSearchParams()?.get("booking")?.trim() || null;   // the Calls list links to /desk/bookings?booking=A56
  const [picked, setPicked] = useState<string | null>(null);          // a row the person opened
  const [closedLink, setClosedLink] = useState<string | null>(null);  // the linked booking whose drawer was closed or whose notice was dismissed
  const [widened, setWidened] = useState(false);                      // show every front desk so the linked booking's schedule is on the page
  const [pref, setPref] = useState<string>(ALL_DESKS);
  useEffect(() => {
    // The choice lives in this browser; it is read after mount so the server and the browser render the same first frame.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPref(readPref() ?? ALL_DESKS);
  }, []);
  const list = agents.data;
  const chosenDesk = pref !== ALL_DESKS && list?.some((a) => a.id === pref) ? pref : ALL_DESKS;
  const desk = widened ? ALL_DESKS : chosenDesk;
  const choose = (v: string) => {
    setWidened(false);
    setPref(v);
    writePref(v);
  };

  const schedules = useSchedules(desk === ALL_DESKS ? null : desk);
  const bookings = useScheduleBookings(null);
  const allBookings = bookings.data;
  const now = useNow();
  const deskName = (id: string | null) => list?.find((a) => a.id === id)?.name ?? null;

  const activeId = picked ?? (linked && linked !== closedLink ? linked : null);
  const openBooking = useMemo(
    () => (activeId ? allBookings.find((b) => b.id?.toLowerCase() === activeId.toLowerCase()) ?? null : null),
    [allBookings, activeId]
  );
  const loaded = !bookings.isLoading && !bookings.error;
  const unknownLink = !!linked && linked !== closedLink && !picked && loaded && !openBooking;
  const closeDrawer = () => {
    if (picked) setPicked(null);
    if (linked) { setClosedLink(linked); clearBookingParam(); }
  };

  const upcoming = useMemo(() => {
    const ids = new Set((schedules.data ?? []).map((s) => s.id));
    const when = (b: (typeof allBookings)[number]) => (b.start ? new Date(b.start).getTime() : 0);
    return allBookings
      .filter((b) => (b.scheduleId ? ids.has(b.scheduleId) : desk === ALL_DESKS || b.agentId === desk))
      .filter((b) => b.status === "booked" && when(b) >= now - 3600_000)
      .sort((a, b) => when(a) - when(b));
  }, [allBookings, schedules.data, desk, now]);

  // A linked booking on another front desk: show every front desk, then bring its row (or its schedule) into view.
  const brought = useRef<string | null>(null);
  useEffect(() => {
    if (!openBooking?.id || brought.current === openBooking.id) return;
    if (chosenDesk !== ALL_DESKS && openBooking.agentId && openBooking.agentId !== chosenDesk) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setWidened(true);
      return;
    }
    const raf = requestAnimationFrame(() => {
      const target =
        document.querySelector(`[data-row-id="${CSS.escape(openBooking.id!)}"]`) ??
        (openBooking.scheduleId ? document.getElementById(`schedule-${openBooking.scheduleId}`) : null);
      if (target) {
        brought.current = openBooking.id;
        target.scrollIntoView?.({ block: "center" });
      }
    });
    return () => cancelAnimationFrame(raf);
  }, [openBooking, chosenDesk, schedules.data, upcoming]);

  const noDesks = !agents.isLoading && !agents.error && (list?.length ?? 0) === 0 && !schedules.isLoading && (schedules.data?.length ?? 0) === 0;
  const newHref = desk === ALL_DESKS ? undefined : desk;

  return (
    <PageShell width="wide" className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-0 space-y-3">
          <h1 className="flex flex-wrap items-center gap-x-3 gap-y-2 font-display text-[clamp(1.75rem,2.6vw,2.5rem)] font-normal leading-[1.08] tracking-[-0.005em] text-text-primary">
            Bookings for
            <FrontDeskSelect agents={list ?? []} value={desk} onChange={choose} />
          </h1>
          <p className="max-w-prose text-[15px] leading-relaxed text-text-secondary">
            A schedule is the set of times a front desk can book. Open one to see it like a calendar and mark times busy.
          </p>
        </div>
      </header>

      {unknownLink && (
        <p role="status" className="flex items-center justify-between gap-3 rounded-lg border border-border bg-bg-surface px-3 py-2.5 text-sm text-text-secondary">
          <span>Booking {linked} was not found. It may be older than the list shows, or the link may be out of date.</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => { setClosedLink(linked); clearBookingParam(); }}
            className="grid size-8 shrink-0 place-items-center rounded-md text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
          >
            <X aria-hidden className="size-4" />
          </button>
        </p>
      )}

      {agents.error && !agents.isLoading && (
        <ErrorState title="Could not load your front desks" message={plainError(agents.error, "Check that the server is running.")} onRetry={() => void agents.mutate()} />
      )}

      {noDesks ? (
        <EmptyState
          icon={<CalendarPlus />}
          title="Set up a front desk first"
          description="Schedules belong to a front desk, the front desk that answers your calls. Create one, then come back to add its schedule."
          action={<LinkButton href="/desk/receptionist">Set up a front desk</LinkButton>}
        />
      ) : (
        <section aria-labelledby="schedules-title" className="space-y-3">
          <h2 id="schedules-title" className="text-sm font-semibold text-text-primary">Schedules</h2>
          {schedules.error && !schedules.isLoading ? (
            <ErrorState title="Could not load the schedules" message={plainError(schedules.error, "Check that the server is running.")} onRetry={() => void schedules.mutate()} />
          ) : schedules.isLoading || !schedules.data ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true" aria-label="Loading schedules">
              <Skeleton className="h-56 w-full rounded-2xl" />
              <Skeleton className="hidden h-56 w-full rounded-2xl sm:block" />
            </div>
          ) : schedules.data.length === 0 ? (
            <EmptyState
              icon={<CalendarCheck />}
              title="No schedules yet"
              description="A schedule says which days and hours callers can book, and how long each slot is. The front desk only offers times that are free on it."
              action={
                <LinkButton href={`/desk/bookings/new${newHref ? `?agent=${encodeURIComponent(newHref)}` : ""}`}>
                  <Plus aria-hidden className="size-4" /> Create a schedule
                </LinkButton>
              }
            />
          ) : (
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {schedules.data.map((s) => (
                <li key={s.id} id={`schedule-${s.id}`} className="flex scroll-mt-24">
                  <div className="flex w-full">
                    <ScheduleCard schedule={s} now={now} deskName={desk === ALL_DESKS ? deskName(s.agent_id) : null} />
                  </div>
                </li>
              ))}
              <li className="flex">
                <div className="flex w-full">
                  <NewScheduleCard agentId={newHref} />
                </div>
              </li>
            </ul>
          )}
        </section>
      )}

      {!noDesks && (schedules.data?.length ?? 0) > 0 && (
        <section aria-labelledby="upcoming-title" className="space-y-3">
          <h2 id="upcoming-title" className="text-sm font-semibold text-text-primary">Upcoming bookings</h2>
          {bookings.isLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : bookings.error ? (
            <ErrorState title="Could not load the bookings" message={plainError(bookings.error, "Check that the server is running.")} onRetry={() => void bookings.mutate()} />
          ) : (
            <BookingExplorer
              bookings={upcoming}
              onOpen={(b) => setPicked(b.id)}
              openId={openBooking?.id ?? null}
              deskName={(b) => b.agentName ?? deskName(b.agentId ?? null)}
              emptyTitle="Nothing booked yet"
              emptyText="When the front desk books a time on a call, or you book someone from a calendar, it appears here."
            />
          )}
        </section>
      )}
      {openBooking && <BookingDrawer booking={openBooking} onClose={closeDrawer} onChanged={() => void bookings.mutate()} />}
    </PageShell>
  );
}

export default function BookingsPage() {
  return (
    <ErrorBoundary>
      {/* useSearchParams needs a Suspense boundary for static rendering */}
      <Suspense fallback={null}>
        <BookingsContent />
      </Suspense>
    </ErrorBoundary>
  );
}
