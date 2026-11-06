"use client";

import { useState, useSyncExternalStore, type ReactNode } from "react";
import { BellOff, BellRing, CheckCircle2, CircleAlert, CircleDashed, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { Badge, Button, Card } from "@/components/ui";
import type { BadgeTone } from "@/components/ui/Badge";
import { QrCode } from "@/components/live/QrCode";
import { API_URL } from "@/lib/apiBase";
import {
  alertPermission,
  installDesk,
  showAlert,
  turnAlertsOff,
  turnAlertsOn,
  useAlertsWanted,
  useInstallState,
  type AlertPermission,
} from "@/lib/deskAlerts";

function Row({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[13rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm text-text-secondary">{label}</dt>
      <dd className="min-w-0 text-sm text-text-primary">
        {children}
        {note && <p className="mt-1 text-xs text-text-secondary">{note}</p>}
      </dd>
    </div>
  );
}

function Status({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "warning" || tone === "error" ? CircleAlert : CircleDashed;
  return (
    <Badge tone={tone} dot>
      <Icon className="size-3" aria-hidden /> {children}
    </Badge>
  );
}

const subscribeNever = () => () => {};
/** The desk's own address as this browser sees it; "" on the server. */
const useOrigin = () => useSyncExternalStore(subscribeNever, () => window.location.origin, () => "");
const isLocalHost = (origin: string) => /\/\/(localhost|127\.0\.0\.1|\[::1\])(:|$)/.test(origin);

/**
 * Settings, "This phone": put the desk on the owner's phone and turn on a ping for every new booking.
 * What it can and cannot do is in docs/DESK_FEATURES.md (no push server: alerts arrive while the desk is open).
 */
export function PhoneDeskSettings() {
  const wanted = useAlertsWanted();
  const install = useInstallState();
  const origin = useOrigin();
  // Read after mount (the server cannot know); refreshed after each answer from the browser.
  const browserPermission = useSyncExternalStore(subscribeNever, alertPermission, () => "default" as AlertPermission);
  const [answered, setAnswered] = useState<AlertPermission | null>(null);
  const permission = answered ?? browserPermission;
  const [busy, setBusy] = useState(false);
  const on = wanted && permission === "granted";

  const enable = async () => {
    setBusy(true);
    try {
      const answer = await turnAlertsOn();
      setAnswered(answer);
      if (answer === "granted") toast.success("Booking alerts are on for this device");
      else if (answer === "denied") toast.error("The browser blocked alerts. Allow them in the site settings, then try again.");
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    const shown = await showAlert({ title: "New booking: Test caller", body: "This is how a booking will look on this device.", url: "/desk/bookings", tag: "test-alert" });
    if (!shown) toast.error("This device could not show the alert.");
  };

  const deskUrl = origin ? `${origin}/desk` : "";

  return (
    <Card aria-labelledby="this-phone" className="space-y-1">
      <div className="border-b border-border pb-4">
        <h2 id="this-phone" className="font-display text-lg font-semibold text-text-primary">This phone</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Keep the desk on your phone&rsquo;s home screen and get a ping when the front desk books someone. Each phone or computer is set up on its own.
        </p>
      </div>
      <dl className="divide-y divide-border">
        <Row
          label="Booking alerts"
          note={
            permission === "unsupported"
              ? "This browser cannot show alerts. On an iPhone, add the desk to the home screen first and open it from there."
              : permission === "denied"
                ? "Alerts were blocked for this site. Allow notifications in the browser's site settings (the icon left of the address), then reload this page."
                : "An alert arrives while the desk is open, even in the background. When it has been closed, opening it says what was booked meanwhile."
          }
        >
          <div className="flex flex-wrap items-center gap-2">
            {permission === "unsupported" ? (
              <Status tone="neutral">Not available here</Status>
            ) : permission === "denied" ? (
              <Status tone="warning">Blocked in this browser</Status>
            ) : on ? (
              <Status tone="success">On</Status>
            ) : (
              <Status tone="neutral">Off</Status>
            )}
            {permission !== "unsupported" && permission !== "denied" && !on && (
              <Button size="sm" loading={busy} onClick={enable} leftIcon={<BellRing className="size-4" aria-hidden />}>
                Turn on alerts
              </Button>
            )}
            {on && (
              <>
                <Button size="sm" variant="outline" onClick={test}>Send a test alert</Button>
                <Button size="sm" variant="ghost" onClick={turnAlertsOff} leftIcon={<BellOff className="size-4" aria-hidden />}>
                  Turn off
                </Button>
              </>
            )}
          </div>
        </Row>

        <Row
          label="On the home screen"
          note={
            install === "app"
              ? "You are using the desk as an app. It opens on Today."
              : install === "offer"
                ? "Adds a Vakya icon that opens the desk full screen, on Today."
                : "Android (Chrome): open the menu ⋮ and choose Add to Home screen. iPhone (Safari): tap Share, then Add to Home Screen."
          }
        >
          {install === "app" ? (
            <Status tone="success">Installed</Status>
          ) : install === "offer" ? (
            <Button
              size="sm"
              leftIcon={<Smartphone className="size-4" aria-hidden />}
              onClick={async () => {
                if (await installDesk()) toast.success("The desk is on your home screen");
              }}
            >
              Install the desk
            </Button>
          ) : (
            <Status tone="neutral">From the browser menu</Status>
          )}
        </Row>

        {deskUrl && install !== "app" && (
          <Row
            label="Open it on your phone"
            note={
              isLocalHost(origin)
                ? "This page is on localhost, which only this computer can open. Open the desk at your https tunnel address and the code will point there. Installing needs https, except on localhost."
                : isLocalHost(API_URL)
                  ? `The desk reads its data from ${API_URL}, which a phone cannot reach. Set NEXT_PUBLIC_API_URL to an address the phone can open (your https tunnel) and restart the web app first.`
                  : "Scan with the phone's camera to open the desk there, then add it to the home screen."
            }
          >
            <div className="flex flex-wrap items-center gap-4">
              <QrCode value={deskUrl} size={116} label={`QR code for ${deskUrl}`} />
              <code className="break-all rounded bg-bg-elevated px-1.5 py-0.5 font-mono text-xs">{deskUrl}</code>
            </div>
          </Row>
        )}
      </dl>
    </Card>
  );
}
