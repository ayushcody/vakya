"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, CalendarX, ChevronLeft, ChevronRight, Pencil } from "lucide-react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { PageShell } from "@/components/ui/PageHeader";
import { BookingExplorer } from "@/components/desk/BookingExplorer";
import { LateBanner } from "@/components/queue/LateBanner";
import { expectedBySlot, useToday } from "@/components/queue/queue";
import { useScheduleBookings } from "@/components/desk/calls";
import { useAgents } from "@/hooks/useApi";
import { useNow } from "@/hooks/useNow";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { chipClass } from "./form/chipStyle";
import {
  DAY_SHORT,
  addDays,
  addMonths,
  bookSlot,
  createBlock,
  dayKeyOf,
  dayLabel,
  deleteBlock,
  hoursInWords,
  isoDate,
  isoHHMM,
  kindLine,
  monthGrid,
  monthLabel,
  nowIn,
  plainError,
  rangeWords,
  schedulesKey,
  slotsKey,
  timeWords,
  useSchedule,
  useSlots,
  weekDays,
  weekRangeLabel,
  weekStart,
  withBlock,
  withoutBlock,
  type CheckOption,
  type Schedule,
  type Slot,
  type SlotsResponse,
} from "@/lib/schedules";
import { AskPanel } from "./AskPanel";
import { BookingDrawer } from "./BookingDrawer";
import { LinkButton } from "./LinkButton";
import { NightsCalendar } from "./NightsCalendar";
import { SlotPopover, type PopoverTarget } from "./SlotPopover";
import { WeekCalendar, cellStyle } from "./WeekCalendar";
import { useMediaQuery } from "./useMediaQuery";

function Legend({ schedule }: { schedule: Schedule }) {
  const items: { state: "free" | "booked" | "full" | "blocked" | "closed" | "past"; label: string }[] = [
    { state: "free", label: "Free" },
    { state: "booked", label: schedule.capacity > 1 ? (schedule.kind === "nights" ? "Some rooms left" : "Partly booked") : "" },
    { state: "full", label: schedule.kind === "nights" ? "No rooms left" : "Booked" },
    { state: "blocked", label: "Busy" },
    { state: "closed", label: "Closed" },
    { state: "past", label: "Past" },
  ];
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-text-secondary" aria-label="What the colours mean">
      {items
        .filter((i) => i.label)
        .map((i) => {
          const look = cellStyle(i.state, schedule.accent, 0.5);
          return (
            <li key={i.state} className="flex items-center gap-1.5">
              <span aria-hidden className={cn("inline-block h-3.5 w-5 rounded-[4px] border border-border", look.className)} style={look.style} />
              {i.label}
            </li>
          );
        })}
    </ul>
  );
}

/** The schedule page: a calendar to read and edit, the caller tester beside it, and the bookings underneath. */
export function ScheduleView({ id }: { id: string }) {
  const { schedule, error, isLoading, notFound, mutate: reloadSchedules } = useSchedule(id);
  if (isLoading || (!schedule && !error && !notFound)) {
    return (
      <PageShell width="full" className="space-y-6">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-[28rem] w-full rounded-2xl" />
      </PageShell>
    );
  }
  if (error && !schedule) {
    return (
      <PageShell width="default">
        <ErrorState title="Could not load this schedule" message={plainError(error, "Check that the server is running.")} onRetry={() => void reloadSchedules()} />
      </PageShell>
    );
  }
  if (!schedule) {
    return (
      <PageShell width="default">
        <EmptyState
          icon={<CalendarX />}
          title="This schedule is not here any more"
          description="It may have been deleted. Go back to the list to pick another one."
          action={<LinkButton href="/desk/bookings">Back to bookings</LinkButton>}
        />
      </PageShell>
    );
  }
  return <Loaded schedule={schedule} />;
}

function Loaded({ schedule }: { schedule: Schedule }) {
  const nights = schedule.kind === "nights";
  const now = useNow();
  const compact = useMediaQuery("(max-width: 767px)");
  const today = now ? nowIn(schedule.timezone, now).date : "";
  const [picked, setPicked] = useState<string | null>(null);
  const anchor = picked ?? today;
  const [highlight, setHighlight] = useState<CheckOption[]>([]);
  const [target, setTarget] = useState<PopoverTarget | null>(null);
  const [drawerId, setDrawerId] = useState<string | null>(null);
  const { mutate: globalMutate } = useSWRConfig();
  const agents = useAgents();
  const deskName = agents.data?.find((a) => a.id === schedule.agent_id)?.name ?? null;

  const range = useMemo(() => {
    if (!anchor) return { from: "", to: "" };
    if (nights) {
      const g = monthGrid(anchor);
      return { from: g[0], to: g[41] };
    }
    const s = weekStart(anchor);
    return { from: s, to: addDays(s, 6) };
  }, [anchor, nights]);
  const slots = useSlots(anchor ? schedule.id : null, range.from, range.to);
  const bookings = useScheduleBookings(schedule.id);
  const queue = useToday(nights ? null : schedule.id);
  const expected = useMemo(() => expectedBySlot(queue.data), [queue.data]);
  const key = slotsKey(schedule.id, range.from, range.to);
  const upcoming = useMemo(
    () =>
      bookings.data
        .filter((b) => b.status === "booked" && (b.start ? new Date(b.end ?? b.start).getTime() >= now - 3600_000 : true))
        .sort((a, b) => new Date(a.start ?? 0).getTime() - new Date(b.start ?? 0).getTime()),
    [bookings.data, now]
  );

  const closePopover = useCallback(() => setTarget(null), []);
  const refreshSummary = () => void globalMutate(schedulesKey());

  async function block(startIso: string, endIso: string) {
    let created = "";
    try {
      await slots.mutate(
        async () => {
          created = (await createBlock(schedule.id, startIso, endIso)).id;
          return api.get<SlotsResponse>(key);
        },
        {
          optimisticData: (cur) => (cur ? withBlock(cur, startIso, endIso, `pending-${startIso}`) : (cur as unknown as SlotsResponse)),
          rollbackOnError: true,
          populateCache: true,
          revalidate: false,
        }
      );
      refreshSummary();
      const label = nights ? dayLabel(isoDate(startIso)) : rangeWords({ start: isoHHMM(startIso), end: isoHHMM(endIso) });
      toast.success(`Marked busy: ${label}`, { action: { label: "Undo", onClick: () => void free(created) } });
    } catch (e) {
      toast.error(plainError(e, "Could not mark that busy. Nothing was changed."));
    }
  }

  async function free(blockId: string) {
    if (!blockId) return;
    try {
      await slots.mutate(
        async () => {
          await deleteBlock(schedule.id, blockId);
          return api.get<SlotsResponse>(key);
        },
        {
          optimisticData: (cur) => (cur ? withoutBlock(cur, blockId, schedule.capacity) : (cur as unknown as SlotsResponse)),
          rollbackOnError: true,
          populateCache: true,
          revalidate: false,
        }
      );
      refreshSummary();
      toast.success("It is free again");
    } catch (e) {
      toast.error(plainError(e, "Could not free that. Nothing was changed."));
    }
  }

  async function book(slot: Slot, input: { name: string; phone: string; nights: number }) {
    await bookSlot({
      schedule_id: schedule.id,
      start: slot.start,
      name: input.name,
      ...(input.phone ? { phone: input.phone } : {}),
      ...(nights ? { nights: input.nights } : {}),
    });
    setTarget(null);
    toast.success(`Booked ${input.name}`);
    void slots.mutate();
    void bookings.mutate();
    refreshSummary();
  }

  const drawerBooking = drawerId ? (bookings.data.find((b) => b.id === drawerId) ?? null) : null;
  /** A click on a slot: a slot taken by one person opens that booking; anything else opens the slot's menu. */
  const openSlot = (slot: Slot, el: HTMLElement, following: Slot[]) => {
    if (slot.status === "full" && slot.bookings.length === 1) setDrawerId(slot.bookings[0].id);
    else setTarget({ slot, el, following });
  };

  const showAnswer = (opts: CheckOption[]) => {
    setHighlight(opts);
    if (opts[0]) setPicked(isoDate(opts[0].start));
  };

  // A new week or month closes anything open on the old one.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTarget(null);
  }, [range.from]);

  const step = nights ? 0 : compact ? 1 : 7;
  const move = (dir: -1 | 1) => setPicked(nights ? addMonths(anchor, dir) : addDays(anchor, dir * step));
  const heading = nights ? monthLabel(anchor || today || "2026-01-01") : compact ? dayLabel(anchor) : weekRangeLabel(anchor);
  const days = compact ? [anchor] : anchor ? weekDays(anchor) : [];

  return (
    <PageShell width="full" className="space-y-6">
      <header className="space-y-3">
        <Link href="/desk/bookings" className="inline-flex min-h-10 items-center gap-1.5 text-sm font-medium text-text-secondary hover:text-text-primary">
          <ArrowLeft aria-hidden className="size-4" /> All schedules
        </Link>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className="font-display text-[clamp(1.75rem,2.6vw,2.5rem)] font-normal leading-[1.08] tracking-[-0.005em] text-text-primary">{schedule.name}</h1>
            <p className="mt-2 max-w-prose text-[15px] leading-relaxed text-text-secondary">
              {kindLine(schedule)}. {hoursInWords(schedule.week, "short")}
              {nights && schedule.check_in && schedule.check_out ? `. Check-in from ${timeWords(schedule.check_in)}, check-out by ${timeWords(schedule.check_out)}` : ""}.
            </p>
          </div>
          <LinkButton href={`/desk/bookings/${encodeURIComponent(schedule.id)}/edit`} variant="outline">
            <Pencil aria-hidden className="size-4" /> Edit schedule
          </LinkButton>
        </div>
      </header>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_21rem]">
        <section aria-label="Calendar" className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1">
              <Button variant="outline" size="sm" aria-label={nights ? "Previous month" : compact ? "Previous day" : "Previous week"} onClick={() => move(-1)} className="w-9 px-0">
                <ChevronLeft aria-hidden className="size-4" />
              </Button>
              <Button variant="outline" size="sm" onClick={() => setPicked(null)}>Today</Button>
              <Button variant="outline" size="sm" aria-label={nights ? "Next month" : compact ? "Next day" : "Next week"} onClick={() => move(1)} className="w-9 px-0">
                <ChevronRight aria-hidden className="size-4" />
              </Button>
            </div>
            <h2 className="order-last min-w-0 basis-full truncate text-[15px] font-semibold text-text-primary sm:order-none sm:flex-1 sm:basis-auto" aria-live="polite">{heading}</h2>
            <label className="ml-auto flex items-center gap-2 text-sm text-text-secondary sm:ml-0">
              <span className="sr-only sm:not-sr-only">Jump to</span>
              <input
                type="date"
                aria-label="Jump to a date"
                value={anchor}
                onChange={(e) => e.target.value && setPicked(e.target.value)}
                className="h-8 rounded-lg border border-border bg-bg-surface px-2 text-sm text-text-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary [@media(pointer:coarse)]:h-10"
              />
            </label>
          </div>

          {compact && !nights && anchor && (
            <div role="group" aria-label="Pick a day" className="grid grid-cols-7 gap-1.5">
              {weekDays(anchor).map((d) => (
                <button
                  key={d}
                  type="button"
                  aria-pressed={d === anchor}
                  aria-label={dayLabel(d)}
                  onClick={() => setPicked(d)}
                  className={chipClass(d === anchor, "flex min-h-14 min-w-0 flex-col items-center justify-center rounded-xl text-xs", d !== anchor && "text-text-secondary")}
                >
                  <span>{DAY_SHORT[dayKeyOf(d)]}</span>
                  <span className="text-base font-semibold tabular-nums">{Number(d.slice(8))}</span>
                </button>
              ))}
            </div>
          )}

          {!nights && <LateBanner scheduleId={schedule.id} queue={queue.data} />}

          <Legend schedule={schedule} />

          {slots.error && !slots.data ? (
            <ErrorState title="Could not load the calendar" message={plainError(slots.error, "Check that the server is running.")} onRetry={() => void slots.mutate()} />
          ) : nights ? (
            <NightsCalendar
              schedule={schedule}
              month={anchor || "2026-01-01"}
              data={slots.data}
              loading={slots.isLoading}
              nowMs={now}
              highlight={highlight.map((h) => h.start)}
              onOpenSlot={(slot, el) => openSlot(slot, el, [])}
            />
          ) : (
            <WeekCalendar
              schedule={schedule}
              days={days}
              data={slots.data}
              loading={slots.isLoading}
              nowMs={now}
              highlight={highlight.map((h) => h.start)}
              compact={compact}
              expected={expected}
              onOpenSlot={openSlot}
              onBlockRange={(s, e) => void block(s, e)}
            />
          )}
          <p className="text-xs text-text-secondary">
            {nights
              ? "Click a night to block it or book someone."
              : compact
                ? "Tap a time for options. Press and hold, then drag down to mark a stretch busy."
                : "Click a free time for options, or press and drag down a day to mark a stretch busy. Escape cancels a drag."}
          </p>
        </section>

        <div className="min-w-0 lg:self-stretch">
          <AskPanel schedule={schedule} onHighlight={showAnswer} className="lg:sticky lg:top-6" />
        </div>
      </div>

      <section aria-labelledby="sched-bookings" className="space-y-3">
        <h2 id="sched-bookings" className="text-sm font-semibold text-text-primary">Upcoming bookings on this schedule</h2>
        {bookings.error ? (
          <ErrorState title="Could not load the bookings" message={plainError(bookings.error, "Check that the server is running.")} onRetry={() => void bookings.mutate()} />
        ) : bookings.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : (
          <BookingExplorer
            bookings={upcoming}
            onOpen={(b) => setDrawerId(b.id)}
            openId={drawerId}
            deskName={() => deskName}
            storageKey="schedule-bookings"
            emptyTitle="Nothing booked yet"
            emptyText="Bookings made on calls, and the ones you add from the calendar, appear here."
          />
        )}
      </section>

      {target && (
        <SlotPopover
          key={target.slot.start}
          schedule={schedule}
          target={target}
          onClose={closePopover}
          onBlock={(end) => {
            setTarget(null);
            void block(target.slot.start, end);
          }}
          onFree={(blockId) => {
            setTarget(null);
            void free(blockId);
          }}
          onBook={(input) => book(target.slot, input)}
          onOpenBooking={(id) => {
            setTarget(null);
            setDrawerId(id);
          }}
        />
      )}
      {drawerBooking && (
        <BookingDrawer
          booking={drawerBooking}
          onClose={() => setDrawerId(null)}
          onChanged={() => {
            void bookings.mutate();
            void slots.mutate();
            refreshSummary();
          }}
        />
      )}
    </PageShell>
  );
}
