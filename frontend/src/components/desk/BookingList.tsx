"use client";

import { useState } from "react";
import Link from "next/link";
import { CalendarCheck, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Badge, Button, EmptyState } from "@/components/ui";
import { BookingDrawer } from "@/components/schedules/BookingDrawer";
import { SOURCE_LABEL, SourceIcon } from "@/components/schedules/bookingWho";
import { bookedByWords } from "@/components/queue/queue";
import { errMessage } from "./format";
import { appointmentLabel, cancelBooking, type Booking } from "./calls";

const CALENDAR_LABEL = { synced: "On the calendar", pending: "Sending to the calendar", not_configured: "Calendar not set up", failed: "Calendar sync failed" } as const;

/**
 * The appointments, each one led by who it is for (their name, large), then when, which schedule and where it came from
 * (an icon: phone call, missed-call callback, browser call or staff). A row opens the booking's drawer (who it is for, the
 * proof of how it was booked, actions). `onChanged` runs after one is cancelled so the page can reload the list.
 */
export function BookingList({
  bookings,
  emptyTitle,
  emptyText,
  onChanged,
}: {
  bookings: Booking[];
  emptyTitle: string;
  emptyText: string;
  onChanged?: () => void;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<Booking | null>(null);

  async function cancel(id: string) {
    setBusy(id);
    try {
      await cancelBooking(id);
      toast.success(`Booking ${id} cancelled`);
      onChanged?.();
    } catch (e) {
      toast.error(errMessage(e, "Could not cancel the booking."));
    } finally {
      setBusy(null);
      setConfirming(null);
    }
  }

  if (bookings.length === 0) {
    return <EmptyState icon={<CalendarCheck />} title={emptyTitle} description={emptyText} />;
  }
  return (
    <>
      <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-bg-surface">
        {bookings.map((b, i) => {
          const cancelled = b.status === "cancelled";
          const title = b.fromCalls ? (b.lines[0] ?? "Appointment booked") : (b.name ?? "A caller");
          const who = b.fromCalls ? null : b.service && b.service !== b.scheduleName ? `with ${b.service}` : null;
          return (
            <li key={b.id ?? b.callId ?? i} className="flex flex-wrap items-start gap-x-4 gap-y-1 px-4 py-3.5 sm:px-5">
              <span aria-hidden title={SOURCE_LABEL[b.source ?? "phone"]} className="mt-1 grid size-10 shrink-0 place-items-center rounded-xl bg-bg-elevated text-text-secondary">
                <SourceIcon source={b.source ?? "phone"} className="size-[18px]" />
              </span>
              <div className="min-w-0 flex-1">
                {b.id && !b.fromCalls ? (
                  <button
                    type="button"
                    onClick={() => setOpen(b)}
                    aria-label={`Open booking ${b.id} for ${title}`}
                    className={`-ml-1 rounded-md px-1 text-left font-display text-[1.375rem] font-normal leading-tight text-text-primary hover:underline hover:decoration-border-3 hover:underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary ${cancelled ? "line-through decoration-text-secondary" : ""}`}
                  >
                    {title}
                  </button>
                ) : (
                  <p className="font-display text-[1.375rem] leading-tight text-text-primary">{title}</p>
                )}
                <p className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[15px] text-text-secondary">
                  {!b.fromCalls && <span className="font-medium text-text-primary">{appointmentLabel(b)}</span>}
                  {!!b.nights && b.nights > 0 && <span>{b.nights} night{b.nights === 1 ? "" : "s"}</span>}
                  {b.token ? <Badge tone="secondary">Token {b.token}</Badge> : null}
                  {b.scheduleName && <Badge tone="primary">{b.scheduleName}</Badge>}
                  {who && <span>{who}</span>}
                  {bookedByWords(b) && <span>{bookedByWords(b)}</span>}
                  {cancelled && <Badge tone="neutral">Cancelled</Badge>}
                  {b.practice && <Badge>Practice call</Badge>}
                  {b.hasEvidence && !cancelled && (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-accent-success"><ShieldCheck aria-hidden className="size-3.5" />Proof kept</span>
                  )}
                </p>
                {b.lines.slice(1).map((l) => (
                  <p key={l} className="mt-0.5 text-sm text-text-secondary">{l}</p>
                ))}
                <p className="mt-1 text-sm text-text-secondary">
                  {b.id && <>{b.id} · </>}
                  {b.phoneMasked ?? b.phone ? <>{b.phoneMasked ?? b.phone} · </> : null}
                  {b.reason && <>{b.reason} · </>}
                  {SOURCE_LABEL[b.source ?? "phone"]}
                  {b.agentName && <> · {b.agentName}</>}
                  {b.calendar && !cancelled && b.calendar !== "not_configured" && <> · {CALENDAR_LABEL[b.calendar]}</>}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {b.callId && (
                  <Link
                    href={`/desk/calls?call=${encodeURIComponent(b.callId)}`}
                    className="inline-flex min-h-10 items-center text-sm font-medium text-accent-primary underline-offset-4 hover:underline"
                  >
                    Open the call
                  </Link>
                )}
                {b.id && !cancelled && confirming !== b.id && (
                  <Button size="sm" variant="ghost" onClick={() => setConfirming(b.id)} aria-label={`Cancel booking ${b.id}`}>
                    Cancel
                  </Button>
                )}
                {b.id && !cancelled && confirming === b.id && (
                  <>
                    <Button size="sm" variant="outline" disabled={busy === b.id} onClick={() => void cancel(b.id!)}>
                      Yes, cancel it
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                      Keep
                    </Button>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {open && <BookingDrawer booking={open} onClose={() => setOpen(null)} onChanged={onChanged} />}
    </>
  );
}
