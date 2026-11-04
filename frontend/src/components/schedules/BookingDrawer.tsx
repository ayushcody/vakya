"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import useSWR from "swr";
import { CalendarClock, Fingerprint, Lock, Globe2, Repeat, Ticket, UserRound, Users, X } from "lucide-react";
import { toast } from "sonner";
import { Badge, Button, Skeleton } from "@/components/ui";
import { BookingTimeline } from "@/components/verify/BookingTimeline";
import type { BookingEvidence, EvidenceItem } from "@/components/verify/types";
import { errMessage } from "@/components/desk/format";
import { bookedByWords } from "@/components/queue/queue";
import { cancelBooking, useScheduleBookings, type Booking } from "@/components/desk/calls";
import { api, ApiError } from "@/lib/api";
import { APP_FONT_CLASSES } from "@/lib/appFonts";
import { formatDateTime } from "@/lib/formatDate";
import { languageName, ordinal, SOURCE_LABEL, SourceIcon } from "./bookingWho";

const noop = () => () => {};
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * Everything about one booking, in this order: who it is for (name, masked phone, language, whether they have called
 * before, who else uses the number, where it came from with a link to the call), the proof of how it was made (the
 * caller's words, the read-back, the yes, each with a play button, and "Receipt verified"), then what you can do.
 */
export function BookingDrawer({ booking, onClose, onChanged }: { booking: Booking; onClose: () => void; onChanged?: () => void }) {
  const hydrated = useSyncExternalStore(noop, () => true, () => false);
  const reduce = useReducedMotion();
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [seals, setSeals] = useState(false);   // the hashes of the proof, behind a toggle so the timeline reads cleanly

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const raf = requestAnimationFrame(() => panel.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus());
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const nodes = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((n) => n.offsetParent !== null);
      if (!nodes.length) return;
      if (e.shiftKey && document.activeElement === nodes[0]) {
        e.preventDefault();
        nodes[nodes.length - 1].focus();
      } else if (!e.shiftKey && document.activeElement === nodes[nodes.length - 1]) {
        e.preventDefault();
        nodes[0].focus();
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", key);
      document.body.style.overflow = overflow;
      before?.focus?.();
    };
  }, []);

  const all = useScheduleBookings(null);
  const sameNumber = useMemo(
    () => (booking.callerRef ? all.data.filter((b) => b.callerRef === booking.callerRef && b.id !== booking.id && b.status === "booked") : []),
    [all.data, booking.callerRef, booking.id]
  );
  const otherNames = useMemo(() => Array.from(new Set(sameNumber.map((b) => b.name).filter((n): n is string => !!n && n !== booking.name))), [sameNumber, booking.name]);

  const evidence = useSWR<BookingEvidence, ApiError>(
    booking.hasEvidence && booking.id ? `/api/bookings/${encodeURIComponent(booking.id)}/evidence` : null,
    (p: string) => api.get<BookingEvidence>(p),
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );
  const audioUrl = (item: EvidenceItem) => (item.audio_available && booking.id ? `/api/bookings/${encodeURIComponent(booking.id)}/evidence/audio/${item.id}` : null);
  const fetchAudio = async (url: string) => URL.createObjectURL(await api.blob(url));

  async function setPublic(value: boolean) {
    if (!booking.id) return;
    try {
      await api.post(`/api/bookings/${encodeURIComponent(booking.id)}/evidence/public`, { public: value });
      await evidence.mutate();
      toast.success(value ? "The proof is public: anyone with the receipt can read and hear it." : "The proof is private again.");
    } catch (e) {
      toast.error(errMessage(e, "Could not change that."));
    }
  }

  async function cancel() {
    if (!booking.id) return;
    setBusy(true);
    try {
      await cancelBooking(booking.id);
      toast.success(`Booking ${booking.id} cancelled`);
      onChanged?.();
      onClose();
    } catch (e) {
      toast.error(errMessage(e, "Could not cancel the booking."));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  if (!hydrated) return null;
  const cancelled = booking.status === "cancelled";
  const lang = languageName(booking.language);
  const count = booking.callerBookingsCount ?? 0;
  const fromCall = booking.source && booking.source !== "staff" && booking.callId;

  return createPortal(
    <div className={`fixed inset-0 z-[100] ${APP_FONT_CLASSES}`}>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: reduce ? 0 : 0.18 }}
        onClick={onClose}
        aria-hidden
        className="absolute inset-0 bg-black/30"
      />
      <motion.aside
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={`Booking ${booking.id ?? ""} for ${booking.name ?? "a caller"}`}
        initial={reduce ? false : { x: 40, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
        className="absolute inset-y-0 right-0 flex w-full flex-col border-l border-border bg-bg-surface shadow-[0_0_60px_-20px_rgba(0,0,0,0.4)] sm:max-w-[30rem]"
      >
        <header className="border-b border-border px-5 pb-4 pt-5 sm:px-6">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className={`font-display text-[1.9rem] font-normal leading-[1.1] text-text-primary ${cancelled ? "line-through decoration-text-secondary" : ""}`}>{booking.name ?? "A caller"}</h2>
              <p className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[15px] text-text-secondary">
                {booking.phoneMasked && <span className="tabular-nums text-text-primary">{booking.phoneMasked}</span>}
                {lang && <span>{lang}</span>}
                {cancelled && <Badge tone="neutral">Cancelled</Badge>}
              </p>
            </div>
            <button type="button" data-autofocus onClick={onClose} aria-label="Close" className="-mr-2 -mt-1 grid size-11 shrink-0 place-items-center rounded-lg text-text-secondary hover:bg-bg-base focus-visible:outline-2 focus-visible:outline-accent-primary">
              <X aria-hidden className="size-5" />
            </button>
          </div>
          <ul className="mt-3.5 space-y-2 text-sm text-text-primary">
            {count > 1 && (
              <li className="flex items-center gap-2"><Repeat aria-hidden className="size-4 shrink-0 text-text-secondary" />Returning caller · {ordinal(count)} booking</li>
            )}
            {booking.token ? (
              <li className="flex items-center gap-2"><Ticket aria-hidden className="size-4 shrink-0 text-text-secondary" />Token {booking.token} of the day</li>
            ) : null}
            {bookedByWords(booking) && (
              <li className="flex items-center gap-2"><UserRound aria-hidden className="size-4 shrink-0 text-text-secondary" />{bookedByWords(booking)!.replace(/^b/, "B")}</li>
            )}
            {otherNames.length > 0 && (
              <li className="flex items-center gap-2"><Users aria-hidden className="size-4 shrink-0 text-text-secondary" />Also booked on this number: {otherNames.join(", ")}</li>
            )}
            <li className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <SourceIcon source={booking.source ?? "phone"} className="size-4 shrink-0 text-text-secondary" />
              <span>{SOURCE_LABEL[booking.source ?? "phone"]}</span>
              {fromCall && (
                <Link href={`/desk/calls?call=${encodeURIComponent(booking.callId as string)}`} className="font-medium text-accent-primary underline-offset-4 hover:underline">
                  Open the transcript
                </Link>
              )}
            </li>
            <li className="flex items-center gap-2"><CalendarClock aria-hidden className="size-4 shrink-0 text-text-secondary" /><span><span className="font-medium">{formatDateTime(booking.start, booking.timezone) || "Time not recorded"}</span>{booking.scheduleName ? ` · ${booking.scheduleName}` : ""}{booking.id ? ` · ${booking.id}` : ""}</span></li>
          </ul>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-[13px] font-semibold uppercase tracking-[1px] text-text-secondary">How it was booked</h3>
            {booking.hasEvidence && evidence.data && (
              <button
                type="button"
                aria-pressed={seals}
                onClick={() => setSeals((v) => !v)}
                className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm font-medium text-text-secondary hover:bg-bg-base hover:text-text-primary focus-visible:outline-2 focus-visible:outline-accent-primary"
              >
                <Fingerprint aria-hidden className="size-4" />
                {seals ? "Hide seals" : "Show seals"}
              </button>
            )}
          </div>
          {!booking.hasEvidence ? (
            <p className="rounded-xl border border-dashed border-border p-4 text-sm text-text-secondary">
              {booking.source === "staff" ? "Staff made this booking from the calendar, so there is no call to prove." : "No recording of how this booking was made was kept. Bookings made on calls after proof was switched on have one."}
            </p>
          ) : evidence.isLoading ? (
            <Skeleton className="h-48 w-full rounded-xl" />
          ) : evidence.error || !evidence.data ? (
            <p role="alert" className="text-sm text-accent-error">{evidence.error ? errMessage(evidence.error, "Could not load the proof.") : "Could not load the proof."}</p>
          ) : (
            <>
              <BookingTimeline evidence={evidence.data} audioUrl={audioUrl} fetchAudio={fetchAudio} seals={seals} />
              {!evidence.data.deleted && (
                <div className="mt-5 flex items-start gap-3 rounded-xl border border-border bg-bg-base p-3.5 text-sm">
                  {evidence.data.public ? <Globe2 aria-hidden className="mt-0.5 size-4 shrink-0" /> : <Lock aria-hidden className="mt-0.5 size-4 shrink-0" />}
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-text-primary">{evidence.data.public ? "Public proof" : "Private proof"}</p>
                    <p className="mt-0.5 text-text-secondary">
                      {evidence.data.public
                        ? "Anyone who has the receipt can read the words and hear the recordings on the Verify page."
                        : "On the public Verify page the words are hidden and there is no audio, only the hashes and the verified seal."}
                    </p>
                    <Button className="mt-2.5" size="sm" variant="outline" onClick={() => void setPublic(!evidence.data!.public)}>
                      {evidence.data.public ? "Make private" : "Make public"}
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <footer className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-3.5 sm:px-6">
          {booking.callerRef && booking.source !== "staff" && (
            <Link href={`/desk/callers?caller=${encodeURIComponent(booking.callerRef)}`} className="inline-flex min-h-10 items-center rounded-lg border border-border px-3 text-sm font-medium text-text-primary hover:bg-bg-base">
              Open the caller
            </Link>
          )}
          {booking.receiptId && booking.hasEvidence && (
            <Link href={`/verify?receipt=${encodeURIComponent(booking.receiptId)}`} className="inline-flex min-h-10 items-center rounded-lg border border-border px-3 text-sm font-medium text-text-primary hover:bg-bg-base">
              Public proof page
            </Link>
          )}
          <span className="flex-1" />
          {!cancelled && booking.id && !confirming && (
            <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>Cancel booking</Button>
          )}
          {!cancelled && booking.id && confirming && (
            <>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void cancel()}>Yes, cancel it</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Keep</Button>
            </>
          )}
        </footer>
      </motion.aside>
    </div>,
    document.body
  );
}
