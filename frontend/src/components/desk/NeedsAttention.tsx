"use client";

import Link from "next/link";
import { ArrowRight, CheckCircle2, CircleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui";
import { useAgents, useEngines, useHealth } from "@/hooks/useApi";
import { usePhoneConnected } from "@/components/agents/agentStatus";
import { useCalls } from "./calls";

interface Item {
  id: string;
  text: ReactNode;
  href?: string;
  cta?: string;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Things that need the owner now, each from real data: nothing is listed that is not true. */
export function NeedsAttention() {
  const agents = useAgents();
  const calls = useCalls(7, 100);
  const phone = usePhoneConnected();
  const health = useHealth();
  const engines = useEngines();

  if (agents.isLoading && !agents.data) return <Skeleton className="h-24 w-full rounded-xl" />;

  const items: Item[] = [];
  const list = agents.data ?? [];
  if (agents.data && list.length === 0) {
    items.push({ id: "no-agent", text: "No front desk is set up yet", href: "/desk/receptionist", cta: "Set one up" });
  } else if (list.length > 0 && list.every((a) => a.document_count === 0)) {
    items.push({
      id: "no-docs",
      text: "The front desk has no documents, so it cannot answer questions about fees or timings",
      href: `/desk/receptionist?agent=${encodeURIComponent(list[0].id)}&tab=documents`,
      cta: "Add documents",
    });
  }
  if (list.length > 0 && phone === false) {
    items.push({ id: "phone", text: "Your phone line is not connected, so nobody can ring the front desk yet", href: "/desk/receptionist?tab=phone", cta: "Connect phone" });
  }
  const handedOff = (calls.data?.calls ?? []).filter((c) => !c.practice && (c.outcome.key === "handed_off" || c.outcome.key === "couldnt_answer")).length;
  if (handedOff > 0) {
    items.push({
      id: "handed-off",
      text: `${plural(handedOff, "call needs", "calls need")} a person or could not be answered in the last 7 days`,
      href: "/desk/calls",
      cta: "See calls",
    });
  }
  if (health.data && health.data.llm && !health.data.llm.active) {
    items.push({ id: "no-llm", text: "The front desk cannot reply yet because no language model is set up", href: "/desk/settings", cta: "See how to fix" });
  }
  if (health.error && !health.data) {
    items.push({ id: "server", text: "Vakya cannot reach its server right now", href: "/desk/settings", cta: "Check" });
  }
  const timbre = (engines.data ?? []).find((e) => e.id === "timbre");
  if (timbre && timbre.available === false) {
    items.push({ id: "gnani", text: "No Gnani key is set, so the front desk cannot hear or speak yet", href: "/desk/settings", cta: "See Settings" });
  }

  return (
    <section aria-labelledby="attention-title">
      <h2 id="attention-title" className="mb-2 text-sm font-semibold text-text-primary">
        Needs your attention
        {items.length > 0 && (
          <span className="ml-2 rounded-full bg-accent-warning/15 px-2 py-0.5 text-xs text-warning-text">{items.length}</span>
        )}
      </h2>
      {items.length === 0 ? (
        <p className="flex items-center gap-2 rounded-2xl border border-border bg-bg-surface px-4 py-3 text-sm text-text-secondary">
          <CheckCircle2 className="size-4 shrink-0 text-accent-success" aria-hidden />
          Nothing needs your attention right now.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-2xl border border-border bg-bg-surface">
          {items.map((it) => (
            <li key={it.id} className="flex items-center gap-3 px-4 py-3">
              <CircleAlert className="size-4 shrink-0 text-warning-text" aria-hidden />
              <p className="min-w-0 flex-1 text-sm text-text-primary">{it.text}</p>
              {it.href && (
                <Link
                  href={it.href}
                  className="inline-flex min-h-11 shrink-0 items-center gap-1 rounded-full px-2 text-sm font-medium text-accent-primary hover:underline sm:min-h-10"
                >
                  {it.cta}
                  <ArrowRight className="size-3.5" aria-hidden />
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
