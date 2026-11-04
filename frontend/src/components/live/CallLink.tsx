"use client";

import { useState } from "react";
import useSWR from "swr";
import { Check, Copy, Link2, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { errMessage } from "@/components/desk/format";
import type { WebCallLink, WebCallLinks } from "@/components/agents/WebCallPanel";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { QrCode } from "./QrCode";

/** "Scan to call from your phone": the QR code of the front desk's web call link, with the link under it. */
export function CallLink({ agentId, className }: { agentId: string; className?: string }) {
  const path = `/api/agents/${agentId}/web-call-links`;
  const links = useSWR<WebCallLinks>(path, (p: string) => api.get<WebCallLinks>(p), { revalidateOnFocus: false, shouldRetryOnError: false });
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const link: WebCallLink | undefined = links.data?.links.find((l) => l.status === "active");

  async function create() {
    if (!links.data) return;
    setBusy(true);
    try {
      const d = links.data.defaults;
      await api.post<WebCallLink>(path, { label: "Demo", expires_in: d.expires_in, max_session_minutes: d.max_session_minutes, max_minutes_per_day: d.max_minutes_per_day });
      await links.mutate();
    } catch (e) {
      toast.error(errMessage(e, "Could not create the link."));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Could not copy. Select the link and copy it by hand.");
    }
  }

  return (
    <div className={cn("min-w-0 max-w-full", className)}>
      <div className="flex min-w-0 items-center gap-4 sm:gap-5">
        <div className="shrink-0">
          {link ? (
            <QrCode value={link.url} size={80} label="QR code of the web call link" />
          ) : (
            <div className="grid size-[80px] place-items-center rounded-lg border border-dashed border-border text-text-secondary">
              <Smartphone aria-hidden className="size-7" />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-display text-[1.125rem] leading-tight text-text-primary sm:text-[1.25rem]">Scan to call from your phone</p>
          {link ? (
            <>
              <p className="mt-1 hidden break-all font-mono text-[11px] leading-snug text-text-secondary sm:block sm:truncate sm:text-xs" title={link.url}>{link.url}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button size="sm" variant="outline" onClick={() => void copy()} leftIcon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}>
                  {copied ? "Copied" : "Copy link"}
                </Button>
                {links.data && !links.data.public && (
                  <span className="hidden text-xs text-text-secondary sm:inline">This link opens only on this computer until a public address is set.</span>
                )}
              </div>
            </>
          ) : (
            <>
              <p className="mt-1 text-sm text-text-secondary">{links.isLoading ? "Looking for the call link..." : "No call link yet. Make one and the code appears here."}</p>
              {!links.isLoading && (
                <Button className="mt-2" size="sm" loading={busy} onClick={() => void create()} leftIcon={<Link2 className="size-3.5" />}>
                  Make a call link
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
