"use client";

import { useMemo, useState } from "react";
import { Check, Copy, PhoneMissed } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { whenLabel } from "@/app/(app)/desk/calls/format";
import { groupMissedCalls, useMissedCallLink, useMissedCalls, type MissedCallLine } from "@/lib/missedCalls";

const TONE: Record<MissedCallLine["status"], "success" | "warning" | "neutral" | "error"> = {
  called_back: "success",
  received: "neutral",
  skipped: "warning",
  failed: "error",
};

function CopyUrl({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* the field can still be selected by hand */
    }
  }
  return (
    <div className="flex gap-2">
      <input
        readOnly
        value={url}
        aria-label="Missed-call link"
        onFocus={(e) => e.currentTarget.select()}
        className="min-h-10 w-full min-w-0 rounded-lg border border-border bg-bg-primary px-3 py-2 font-mono text-xs text-text-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
      />
      <Button size="sm" variant="outline" onClick={() => void copy()} aria-label="Copy missed-call link" leftIcon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}>
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

/**
 * The missed-call callback of a receptionist: the link to use as the Answer URL of a Vobiz missed-call number, whether callbacks
 * are on, and the last ten missed calls with what happened to each.
 */
export function MissedCallPanel({ agentId }: { agentId: string }) {
  const { link, error } = useMissedCallLink(agentId);
  const calls = useMissedCalls();
  const lines = useMemo(() => groupMissedCalls(calls.data?.events ?? [], 10), [calls.data]);
  const enabled = calls.data?.enabled ?? link?.enabled;
  const delay = calls.data?.delay_s ?? link?.delay_s ?? 8;

  return (
    <section aria-labelledby="missed-title" className="space-y-3 border-t border-border pt-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="missed-title" className="flex items-center gap-2 text-base font-semibold text-text-primary">
            <PhoneMissed className="size-4" aria-hidden /> Call back missed calls
          </h3>
          <p className="mt-1 text-sm text-text-secondary">
            When someone rings your missed-call number, the call is turned away before it is answered (so it costs them nothing) and the front desk rings them back about {Math.round(delay)} seconds later.
          </p>
        </div>
        {enabled === true && <Badge tone="success" dot>Callbacks on</Badge>}
        {enabled === false && <Badge tone="warning" dot>Callbacks off</Badge>}
      </div>

      {link ? (
        <div className="space-y-2">
          <CopyUrl url={link.url} />
          <p className="text-sm text-text-secondary">
            In the Vobiz console, create an XML Application with this link as its Answer URL (method POST) and attach a number to it. Vobiz sends the caller&apos;s number here; nothing else is shared.
          </p>
        </div>
      ) : error ? (
        <p role="status" className="text-sm text-text-secondary">
          The link is not available yet: {error} Connect your phone line first, then come back.
        </p>
      ) : (
        <p className="text-sm text-text-secondary" aria-busy="true">Making the link…</p>
      )}
      {enabled === false && (
        <p className="text-sm text-text-secondary">Missed calls are logged below but nobody is called back. Set MISSED_CALL_CALLBACK=on in backend/.env and restart to turn callbacks on.</p>
      )}

      <div>
        <h4 className="text-sm font-semibold text-text-primary">Last missed calls</h4>
        {lines.length === 0 ? (
          <p className="mt-1 text-sm text-text-secondary">No missed calls yet.</p>
        ) : (
          <ul className="mt-2 divide-y divide-border overflow-hidden rounded-xl border border-border">
            {lines.map((l) => (
              <li key={l.hit} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
                <span className="font-mono tabular-nums text-text-primary">{l.number}</span>
                <Badge tone={TONE[l.status]}>{l.status === "called_back" ? "Called back" : l.status === "failed" ? "Failed" : l.status === "skipped" ? "Skipped" : "Received"}</Badge>
                <span className="min-w-0 flex-1 text-text-secondary">{l.text}</span>
                <span className="text-xs text-text-secondary">{whenLabel(l.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
