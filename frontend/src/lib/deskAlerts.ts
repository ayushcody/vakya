"use client";

/**
 * The desk on the owner's phone: booking alerts and "Add to Home screen". Contract: docs/DESK_FEATURES.md.
 *
 * Alerts come from the desk itself while it is open (in a tab, or as the app on the phone's home screen): it already
 * checks the bookings every 30 seconds, and a booking it has not seen before becomes an alert. There is no push server,
 * so a desk that is closed, or a phone that has put the app to sleep, gets nothing until the desk is opened again; then
 * it says what was booked while it was away. The pure rules are tested in lib/__tests__/deskAlerts.test.ts.
 */

import { useSyncExternalStore } from "react";
import type { Booking } from "@/components/desk/calls";
import { appointmentLabel } from "@/components/desk/calls";

/** "on" once the owner turned alerts on on this device. */
export const ALERTS_KEY = "vakya-alerts";
/** ISO time of the newest booking this device has already told the owner about. */
export const SEEN_KEY = "vakya-alerts-seen";
/** More new bookings than this at once become one summary instead of one alert each. */
export const MAX_SINGLE_ALERTS = 3;

/* ------------------------------------------------------------------ pure rules */

/** A booking the owner should hear about: live, made by the front desk (not added by staff), not a practice call. */
export function alertable(b: Booking): boolean {
  return b.status === "booked" && !b.practice && b.source !== "staff" && !!b.id && !!b.createdAt;
}

/**
 * Which bookings are new since `seen` (an ISO time; null on the device's first look, when nothing is new), oldest first,
 * and the new `seen` mark. A booking made in the same millisecond as the mark is not repeated.
 */
export function freshBookings(list: Booking[], seen: string | null): { fresh: Booking[]; seen: string | null } {
  const at = (iso: string) => Date.parse(iso);
  const live = list.filter((b) => alertable(b) && !Number.isNaN(at(b.createdAt!)));
  const mark = seen != null && !Number.isNaN(at(seen)) ? seen : null;
  const newest = live.reduce<string | null>((m, b) => (m == null || at(b.createdAt!) > at(m) ? b.createdAt! : m), mark);
  if (mark == null) return { fresh: [], seen: newest };
  const fresh = live.filter((b) => at(b.createdAt!) > at(mark)).sort((a, b) => at(a.createdAt!) - at(b.createdAt!));
  return { fresh, seen: newest };
}

export interface AlertText {
  title: string;
  body: string;
  url: string;
  tag: string;
}

/** "New booking: Sunita Kulkarni" / "Thu 12 Nov, 7 pm · Dr. Joshi". */
export function alertText(b: Booking): AlertText {
  const who = b.name?.trim() || "a caller";
  const what = b.service || b.scheduleName;
  return {
    title: `New booking: ${who}`,
    body: [appointmentLabel(b), what].filter(Boolean).join(" · "),
    url: b.id ? `/desk/bookings/${encodeURIComponent(b.id)}` : "/desk/bookings",
    tag: `booking-${b.id ?? b.createdAt}`,
  };
}

/** One line for several bookings at once ("4 new bookings while the desk was closed"). */
export function summaryText(count: number): AlertText {
  return {
    title: `${count} new bookings`,
    body: "Booked on calls while the desk was not open. Tap to see them.",
    url: "/desk/bookings",
    tag: "bookings-summary",
  };
}

/* ------------------------------------------------------------------ this device */

export type AlertPermission = "unsupported" | "default" | "granted" | "denied";

export function alertPermission(): AlertPermission {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return Notification.permission as AlertPermission;
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* private mode: alerts still work for this visit */
  }
}

export const readSeen = () => read(SEEN_KEY);
export const writeSeen = (v: string | null) => write(SEEN_KEY, v);

/* The on/off switch, shared by Settings and the alert hook without a page reload. */
const switchListeners = new Set<() => void>();
export function alertsWanted(): boolean {
  return read(ALERTS_KEY) === "on";
}
function setWanted(on: boolean) {
  write(ALERTS_KEY, on ? "on" : null);
  switchListeners.forEach((l) => l());
}
export function useAlertsWanted(): boolean {
  return useSyncExternalStore(
    (l) => {
      switchListeners.add(l);
      return () => switchListeners.delete(l);
    },
    alertsWanted,
    () => false
  );
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
  try {
    return (await navigator.serviceWorker.getRegistration("/")) ?? (await navigator.serviceWorker.register("/sw.js", { scope: "/" }));
  } catch {
    return null;
  }
}

/** Ask the browser once, then remember the choice. Returns what the browser answered. */
export async function turnAlertsOn(): Promise<AlertPermission> {
  if (alertPermission() === "unsupported") return "unsupported";
  const answer = (await Notification.requestPermission()) as AlertPermission;
  if (answer === "granted") {
    await registration();
    setWanted(true);
  }
  return answer;
}

export function turnAlertsOff() {
  setWanted(false);
}

/**
 * Show one alert on this device. Through the service worker when there is one (a phone needs it, and a tap then opens
 * the booking), else a plain browser notification. Returns false when nothing could be shown.
 */
export async function showAlert(text: AlertText): Promise<boolean> {
  if (alertPermission() !== "granted") return false;
  const options: NotificationOptions = { body: text.body, tag: text.tag, icon: "/brand/vakya-app-icon-192.png", badge: "/brand/vakya-app-icon-192.png", data: { url: text.url } };
  const reg = await registration();
  if (reg) {
    try {
      await reg.showNotification(text.title, options);
      return true;
    } catch {
      /* fall through */
    }
  }
  try {
    const n = new Notification(text.title, options);
    n.onclick = () => {
      window.focus();
      window.location.assign(text.url);
    };
    return true;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ Add to Home screen */

/** The event Chrome and Edge fire when the desk can be installed (not in the DOM typings). */
export interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferred: InstallPromptEvent | null = null;
let installed = false;
const installListeners = new Set<() => void>();
const notifyInstall = () => installListeners.forEach((l) => l());

/** Start listening for the browser's install offer. Called once by the desk shell, as early as it can. */
export function watchInstallPrompt() {
  if (typeof window === "undefined" || (window as unknown as { __vakyaInstall?: boolean }).__vakyaInstall) return;
  (window as unknown as { __vakyaInstall?: boolean }).__vakyaInstall = true;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e as InstallPromptEvent;
    notifyInstall();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    installed = true;
    notifyInstall();
  });
}

/** True when the desk is running as the installed app (from the home screen). */
export function runningAsApp(): boolean {
  if (typeof window === "undefined") return false;
  return installed || window.matchMedia?.("(display-mode: standalone)").matches || (navigator as unknown as { standalone?: boolean }).standalone === true;
}

export type InstallState = "app" | "offer" | "manual";

/** "app": already installed; "offer": the browser can install it with one tap; "manual": follow the browser's menu. */
export function useInstallState(): InstallState {
  return useSyncExternalStore(
    (l) => {
      installListeners.add(l);
      return () => installListeners.delete(l);
    },
    () => (runningAsApp() ? "app" : deferred ? "offer" : "manual"),
    () => "manual"
  );
}

/** Show the browser's own install dialog. Returns true when the owner accepted. */
export async function installDesk(): Promise<boolean> {
  if (!deferred) return false;
  const e = deferred;
  deferred = null;
  await e.prompt();
  const { outcome } = await e.userChoice;
  notifyInstall();
  return outcome === "accepted";
}
