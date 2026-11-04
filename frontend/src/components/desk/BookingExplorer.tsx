"use client";

import { useMemo } from "react";
import { CalendarCheck, ShieldCheck } from "lucide-react";
import { Badge, EmptyState } from "@/components/ui";
import { bookedByWords } from "@/components/queue/queue";
import { ListView, type ListColumn } from "@/components/ui/ListView";
import { SOURCE_LABEL, SourceIcon } from "@/components/schedules/bookingWho";
import { dayOfInstant, formatDay, formatTime } from "@/lib/formatDate";
import { cn } from "@/lib/utils";
import { appointmentLabel, type Booking } from "./calls";

const muted = "text-text-secondary";

/** The id a row is known by: the booking id, else the call it was worked out from. */
const rowId = (b: Booking, i = 0) => b.id ?? (b.callId ? `call-${b.callId}` : `row-${i}`);

const nameOf = (b: Booking) => (b.fromCalls ? (b.lines[0] ?? "Appointment booked") : (b.name ?? "A caller"));

function whenValue(b: Booking): number | null {
  const t = b.start ? new Date(b.start).getTime() : NaN;
  return Number.isNaN(t) ? null : t;
}

/** "Today", "Tomorrow" or "Thu 12 Nov": the heading of one day's bookings. */
function dayHeading(day: string, today: string | null): string {
  if (today && day === today) return "Today";
  if (today) {
    const [y, m, d] = today.split("-").map(Number);
    const tomorrow = new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
    if (day === tomorrow) return "Tomorrow";
  }
  return formatDay(day, today ? Number(today.slice(0, 4)) : undefined) ?? day;
}

function columns(deskName: (b: Booking) => string | null): ListColumn<Booking>[] {
  return [
    {
      id: "name", header: "Name", width: "minmax(140px, 1.6fr)", minWidth: 140, required: true,
      stacked: { line: 1, slot: "fill" },
      sortValue: (b) => nameOf(b).toLowerCase(),
      cell: (b) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <span title={nameOf(b)} className={cn("truncate text-sm font-medium text-text-primary", b.status === "cancelled" && "line-through decoration-text-secondary")}>
            {nameOf(b)}
          </span>
          {b.token && b.status !== "cancelled" ? (
            <span className="shrink-0 rounded-md bg-bg-elevated px-1.5 py-0.5 text-xs font-semibold tabular-nums text-text-secondary">Token {b.token}</span>
          ) : null}
          {bookedByWords(b) && <span title={bookedByWords(b)!} className="hidden min-w-0 truncate text-sm text-text-secondary sm:inline">· {bookedByWords(b)}</span>}
          {b.hasEvidence && b.status !== "cancelled" && (
            <span role="img" aria-label="Proof kept" title="Proof kept" className="shrink-0 text-accent-success">
              <ShieldCheck aria-hidden className="size-3.5" />
            </span>
          )}
        </span>
      ),
    },
    {
      id: "when", header: "When", width: ({ grouped }) => (grouped ? "96px" : "176px"), minWidth: ({ grouped }) => (grouped ? 96 : 176), required: true,
      stacked: { line: 2, slot: "fill" },
      sortValue: whenValue,
      cell: (b, { grouped }) => {
        const nights = b.nights && b.nights > 0 ? ` · ${b.nights} night${b.nights === 1 ? "" : "s"}` : "";
        const text = grouped && b.start ? formatTime(b.start, b.timezone) || appointmentLabel(b) : b.fromCalls ? "Time not recorded" : appointmentLabel(b);
        return <span className="block truncate whitespace-nowrap text-sm tabular-nums text-text-primary">{text}{nights}</span>;
      },
    },
    {
      id: "schedule", header: "Schedule", width: "minmax(120px, 1.2fr)", minWidth: 120, priority: 3,
      stacked: { line: 2, slot: "end" },
      sortValue: (b) => (b.scheduleName ?? "").toLowerCase() || null,
      cell: (b) => <span title={b.scheduleName ?? undefined} className={cn("block truncate text-sm", b.scheduleName ? "text-text-primary" : muted)}>{b.scheduleName ?? "—"}</span>,
    },
    {
      id: "desk", header: "Front desk", width: "minmax(112px, 1fr)", minWidth: 112, priority: 4,
      sortValue: (b) => (deskName(b) ?? "").toLowerCase() || null,
      cell: (b) => {
        const name = deskName(b);
        return <span title={name ?? undefined} className={cn("block truncate text-sm", name ? "text-text-primary" : muted)}>{name ?? "—"}</span>;
      },
    },
    {
      id: "source", header: "Source", width: "168px", minWidth: 168, priority: 5,
      stacked: { line: 1, slot: "start" },
      sortValue: (b) => SOURCE_LABEL[b.source ?? "phone"],
      cell: (b, { stacked }) => (
        <span className="flex items-center gap-2 whitespace-nowrap text-sm text-text-primary" title={SOURCE_LABEL[b.source ?? "phone"]}>
          <SourceIcon source={b.source ?? "phone"} className="size-4 shrink-0 text-text-secondary" />
          {!stacked && SOURCE_LABEL[b.source ?? "phone"]}
          {stacked && <span className="sr-only">{SOURCE_LABEL[b.source ?? "phone"]}</span>}
        </span>
      ),
    },
    {
      id: "status", header: "Status", width: "96px", minWidth: 96, required: true,
      stacked: { line: 1, slot: "end" },
      sortValue: (b) => (b.status === "booked" ? 0 : 1),
      cell: (b) => <Badge tone={b.status === "cancelled" ? "neutral" : "success"}>{b.status === "cancelled" ? "Cancelled" : "Booked"}</Badge>,
    },
  ];
}

/**
 * The bookings as an explorer list (components/ui/ListView): Name, When, Schedule, Front desk, Source and Status, grouped by
 * day while sorted by time. A row opens the booking's drawer (who it is for, the proof of how it was booked, cancel) through
 * `onOpen`; the page owns the drawer so a link such as /desk/bookings?booking=A56 can open it too.
 */
export function BookingExplorer({
  bookings, onOpen, openId = null, deskName = (b) => b.agentName, emptyTitle, emptyText, storageKey = "bookings",
}: {
  bookings: Booking[];
  /** A row was opened (click, Enter or Space). Calls-derived rows have no id and no drawer, so they are not passed. */
  onOpen: (b: Booking) => void;
  /** The booking whose drawer is open (highlighted). */
  openId?: string | null;
  deskName?: (b: Booking) => string | null;
  emptyTitle: string;
  emptyText: string;
  storageKey?: string;
}) {
  const cols = useMemo(() => columns(deskName), [deskName]);
  if (bookings.length === 0) return <EmptyState icon={<CalendarCheck />} title={emptyTitle} description={emptyText} />;
  return (
    <ListView<Booking>
      label="Upcoming bookings"
      rows={bookings}
      columns={cols}
      getRowId={(b) => rowId(b, bookings.indexOf(b))}
      selectedId={openId}
      onOpen={(b) => { if (b.id && !b.fromCalls) onOpen(b); }}
      defaultSort={{ id: "when", dir: "asc" }}
      groupBy={(b) => {
        const day = dayOfInstant(b.start, b.timezone);
        return day ? { key: day, label: dayHeading(day, dayOfInstant(new Date(), b.timezone)) } : { key: "unknown", label: "Time not recorded" };
      }}
      storageKey={storageKey}
    />
  );
}
