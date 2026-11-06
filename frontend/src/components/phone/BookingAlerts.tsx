"use client";

import { useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { bookingsKey, fromApiBooking, type BookingsResponse } from "@/components/desk/calls";
import {
  MAX_SINGLE_ALERTS,
  alertText,
  freshBookings,
  readSeen,
  showAlert,
  summaryText,
  useAlertsWanted,
  watchInstallPrompt,
  writeSeen,
  type AlertText,
} from "@/lib/deskAlerts";

/**
 * Mounted once by the desk shell. Every 30 seconds (also while the tab is in the background) it reads the bookings, the
 * same request Today makes, and tells the owner about each one this device has not seen: a toast in the desk, and an alert
 * on the phone or computer when alerts are on and the desk is not in front. Renders nothing.
 */
export function BookingAlerts() {
  const router = useRouter();
  const wanted = useAlertsWanted();
  const key = useMemo(() => bookingsKey(30), []);
  const { data } = useSWR<BookingsResponse>(key, (p: string) => api.get<BookingsResponse>(p), {
    refreshInterval: 30_000,
    refreshWhenHidden: true,
    revalidateOnFocus: true,
    shouldRetryOnError: false,
  });

  useEffect(() => watchInstallPrompt(), []);

  useEffect(() => {
    if (!data) return;
    const { fresh, seen } = freshBookings(data.bookings.map((b) => fromApiBooking(b)), readSeen());
    writeSeen(seen);
    if (!fresh.length) return;
    const texts: AlertText[] = fresh.length > MAX_SINGLE_ALERTS ? [summaryText(fresh.length)] : fresh.map(alertText);
    const away = typeof document !== "undefined" && document.visibilityState !== "visible";
    for (const t of texts) {
      toast.success(t.title, { description: t.body, id: t.tag, action: { label: "Open", onClick: () => router.push(t.url) } });
      if (wanted && away) void showAlert(t);
    }
  }, [data, wanted, router]);

  return null;
}
