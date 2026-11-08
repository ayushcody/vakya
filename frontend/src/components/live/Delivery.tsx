"use client";

import useSWR from "swr";
import { CalendarCheck, CalendarClock, CalendarX, MessageCircle } from "lucide-react";
import { api } from "@/lib/api";
import { bookingsKey } from "@/components/desk/calls";
import { cn } from "@/lib/utils";
import { clockWithSeconds } from "./time";

/** What happened after a booking change: the WhatsApp confirmation and the calendar. Both come from the desk's own records, polled. */
export interface WhatsAppMessage {
  at: string;
  state: string;
  kind: string;
  booking_id: string;
}
export interface BookingSync {
  id: string;
  calendar_sync: "synced" | "pending" | "failed" | "not_configured";
}
export interface Delivery {
  messages: WhatsAppMessage[];
  bookings: BookingSync[];
}

export function useDelivery(enabled: boolean, agentId?: string): Delivery {
  const opts = { refreshInterval: enabled ? 3000 : 0, revalidateOnFocus: false, shouldRetryOnError: false } as const;
  const wa = useSWR<{ messages: WhatsAppMessage[] }>(agentId ? "/api/whatsapp/messages?limit=50" : null, (p: string) => api.get<{ messages: WhatsAppMessage[] }>(p), opts);
  const bk = useSWR<{ bookings: BookingSync[] }>(agentId ? bookingsKey(30) : null, (p: string) => api.get<{ bookings: BookingSync[] }>(p), opts);
  return { messages: wa.data?.messages ?? [], bookings: bk.data?.bookings ?? [] };
}

const KINDS: Record<string, string[]> = { booked: ["booked", "confirmed", "new", "created"], moved: ["moved", "rescheduled"], cancelled: ["cancelled", "canceled"] };

export type Tone = "ok" | "wait" | "bad" | "idle";

/** The WhatsApp message that belongs to this change: the booking's, of that kind, written after the card appeared. */
export function whatsappChip(d: Delivery, bookingId: string, status: string, at: string): { text: string; tone: Tone; at?: string } {
  const since = (Date.parse(at) || 0) - 2000;
  const mine = d.messages
    .filter((m) => m.booking_id === bookingId && (!since || (Date.parse(m.at) || 0) >= since))
    .filter((m) => !KINDS[status] || KINDS[status].includes(m.kind) || !Object.values(KINDS).flat().includes(m.kind));
  const m = mine.sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0))[0];
  if (!m) return { text: "WhatsApp queued", tone: "wait" };
  if (m.state === "sent") return { text: "WhatsApp sent", tone: "ok", at: m.at };
  if (m.state === "preview") return { text: "WhatsApp preview, not sent", tone: "idle", at: m.at };
  if (m.state === "failed") return { text: "WhatsApp failed", tone: "bad", at: m.at };
  if (m.state === "skipped") return { text: "WhatsApp skipped", tone: "idle", at: m.at };
  return { text: "WhatsApp queued", tone: "wait" };
}

export function calendarChip(d: Delivery, bookingId: string, status: string): { text: string; tone: Tone } | null {
  const b = d.bookings.find((x) => x.id === bookingId);
  if (!b) return status === "cancelled" ? null : { text: "Calendar pending", tone: "wait" };
  switch (b.calendar_sync) {
    case "synced": return { text: "Calendar synced", tone: "ok" };
    case "pending": return { text: "Calendar syncing", tone: "wait" };
    case "failed": return { text: "Calendar sync failed", tone: "bad" };
    default: return { text: "Calendar not connected", tone: "idle" };
  }
}

const TONE: Record<Tone, string> = {
  ok: "border-[color-mix(in_srgb,var(--color-accent-success)_40%,transparent)] text-accent-success",
  wait: "border-border text-text-secondary",
  bad: "border-[color-mix(in_srgb,var(--color-accent-error)_45%,transparent)] text-accent-error",
  idle: "border-border text-text-secondary",
};

function Chip({ tone, text, at, icon: Icon, label }: { tone: Tone; text: string; at?: string; icon: typeof MessageCircle; label: string }) {
  return (
    <span role="status" aria-label={label} data-tone={tone} className={cn("inline-flex items-center gap-1.5 rounded-full border bg-bg-surface px-3 py-1 text-[13px] font-medium", TONE[tone])}>
      <span aria-hidden className={cn("size-1.5 rounded-full bg-current", tone === "wait" && "motion-safe:animate-pulse")} />
      <Icon aria-hidden className="size-3.5" />
      {text}
      {at && <span className="font-normal text-text-secondary">{clockWithSeconds(at)}</span>}
    </span>
  );
}

/** The two chips under a booked, moved or cancelled card. */
export function DeliveryChips({ bookingId, status, at, delivery }: { bookingId: string; status: string; at: string; delivery: Delivery }) {
  const w = whatsappChip(delivery, bookingId, status, at);
  const c = calendarChip(delivery, bookingId, status);
  const CalIcon = c?.tone === "bad" ? CalendarX : c?.tone === "ok" ? CalendarCheck : CalendarClock;
  return (
    <div data-testid="delivery-chips" className="mt-2 flex flex-wrap gap-1.5">
      <Chip tone={w.tone} text={w.text} at={w.at} icon={MessageCircle} label={w.text} />
      {c && <Chip tone={c.tone} text={c.text} icon={CalIcon} label={c.text} />}
    </div>
  );
}
