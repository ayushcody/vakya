"use client";

import type { ReactNode } from "react";
import { CheckCircle2, CircleAlert, CircleDashed } from "lucide-react";
import { Badge, Card, Skeleton } from "@/components/ui";
import type { BadgeTone } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/formatDate";
import { KIND_LABEL, STATE_LABEL, useWhatsAppMessages, useWhatsAppStatus, type WhatsAppState } from "@/lib/whatsapp";

const code = "rounded bg-bg-elevated px-1.5 py-0.5 font-mono text-xs";
const TONE: Record<WhatsAppState, BadgeTone> = { sent: "success", preview: "neutral", failed: "error", skipped: "warning" };

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

/** The "WhatsApp confirmations" card of Settings: whether the Cloud API is set up (never the token), the template names and
 *  the recent messages with their honest state. A "preview" was built but not sent. */
export function WhatsAppSettings() {
  const status = useWhatsAppStatus();
  const messages = useWhatsAppMessages();
  const s = status.data && typeof status.data.configured === "boolean" ? status.data : undefined;
  const Icon = s?.configured ? CheckCircle2 : CircleAlert;

  return (
    <Card aria-labelledby="whatsapp" className="space-y-1">
      <div className="border-b border-border pb-4">
        <h2 id="whatsapp" className="font-display text-lg font-semibold text-text-primary">WhatsApp confirmations</h2>
        <p className="mt-1 text-sm text-text-secondary">
          After a call that booked, moved or cancelled, the caller gets one WhatsApp message with the final state, once they have said yes to it. Until Meta approves the templates, the messages are only previewed here.
        </p>
      </div>
      <dl className="divide-y divide-border">
        {status.isLoading ? (
          <Skeleton className="my-3 h-16 w-full" />
        ) : s ? (
          <>
            <Row label="WhatsApp Cloud API">
              <Badge tone={s.configured ? "success" : "warning"} dot>
                <Icon className="size-3" aria-hidden /> {s.configured ? "Set up" : "Not set up: previews only"}
              </Badge>
            </Row>
            {!s.configured && s.missing.length > 0 && (
              <Row label="Missing settings">
                <span className="flex flex-wrap gap-1.5">
                  {s.missing.map((m) => <code key={m} className={code}>{m}</code>)}
                </span>
              </Row>
            )}
            <Row label="Templates" note="Create these three as Utility templates in English, Hindi and Marathi (docs/WHATSAPP.md has the exact texts).">
              <span className="flex flex-wrap gap-1.5">
                {Object.values(s.templates).map((t) => <code key={t} className={code}>{t}</code>)}
              </span>
            </Row>
            <Row label="When it goes" note={`${s.send_delay_s} seconds after the call ends; ${s.backstop_s} seconds after the last change if the call never reports its end.`}>
              {s.confirmations_on ? "On" : "Off (WHATSAPP_CONFIRMATIONS)"}
            </Row>
            <Row label="So far">
              {s.counts.sent} sent, {s.counts.preview} previewed, {s.counts.failed} failed, {s.counts.skipped} skipped; {s.counts.consented_numbers} numbers agreed
            </Row>
          </>
        ) : (
          <Row label="WhatsApp"><Badge tone="neutral" dot><CircleDashed className="size-3" aria-hidden /> Could not load</Badge></Row>
        )}
        <div className="py-3">
          <h3 className="text-sm font-medium text-text-primary">Recent messages</h3>
          {messages.isLoading ? (
            <Skeleton className="mt-3 h-16 w-full" />
          ) : (messages.data?.messages ?? []).length === 0 ? (
            <p className="mt-2 text-sm text-text-secondary">None yet. A message appears here after a call in which a booking changes.</p>
          ) : (
            <ul className="mt-2 divide-y divide-border" data-testid="whatsapp-messages">
              {messages.data!.messages.map((m) => (
                <li key={m.id} className="grid gap-1 py-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
                    <Badge tone={TONE[m.state]} dot>{STATE_LABEL[m.state]}</Badge>
                    <span>{KIND_LABEL[m.kind]}</span>
                    <span>{m.phone_masked}</span>
                    <span>booking {m.booking_id}</span>
                    <span>{formatDateTime(m.at)}</span>
                  </div>
                  {m.text ? <p className="text-sm text-text-primary">{m.text}</p> : null}
                  {(m.error || m.reason) && <p className="text-xs text-text-secondary">{m.error ?? m.reason}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      </dl>
    </Card>
  );
}
