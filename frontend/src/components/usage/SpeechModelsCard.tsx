"use client";

import type { ReactNode } from "react";
import { CheckCircle2, CircleAlert } from "lucide-react";
import { Badge, Card, Skeleton } from "@/components/ui";
import { formatDateTime } from "@/lib/formatDate";
import { cn } from "@/lib/utils";
import { KIND_LABEL, degradedLabel, formatCredits, formatMs, formatPercent, formatRupees, latencyLine } from "./format";
import type { BudgetStatus, ProviderInUse, ProviderState, UsageSummary } from "./types";
import { useUsageSummary } from "./useUsage";

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

const STATE: Record<ProviderState, { tone: "success" | "warning" | "error"; word: string | null }> = {
  ready: { tone: "success", word: null },
  retrying: { tone: "warning", word: "busy, retrying" },
  unavailable: { tone: "error", word: "unavailable" },
  held_back: { tone: "warning", word: "held back" },
  needs_key: { tone: "warning", word: "needs a key" },
};

function ProviderValue({ p }: { p: ProviderInUse }) {
  const st = STATE[p.state] ?? STATE.ready;
  const Icon = st.tone === "success" ? CheckCircle2 : CircleAlert;
  return (
    <>
      <Badge tone={st.tone} dot>
        <Icon className="size-3" aria-hidden /> {p.name}
      </Badge>
      {st.word && <span className="ml-2 text-xs font-medium text-text-secondary">{st.word}</span>}
      <p className="mt-1 text-xs text-text-secondary">{p.why}</p>
    </>
  );
}

/** The credit budget as a bar with the live-call reserve marked at the end of it. */
export function BudgetBar({ budget }: { budget: BudgetStatus }) {
  const total = budget.budget > 0 ? budget.budget : 1;
  const used = Math.min(100, (budget.spent / total) * 100);
  const reserveStart = 100 - budget.reserve_pct;
  const tone = budget.exhausted ? "bg-accent-error" : budget.reserve_reached ? "bg-accent-warning" : "bg-accent-primary";
  const label = `${formatCredits(budget.spent)} of ${formatCredits(budget.budget)} credits used. The last ${formatCredits(budget.reserve)} are kept for live calls.`;
  return (
    <div>
      <div
        role="meter"
        aria-label="Credits used"
        aria-valuemin={0}
        aria-valuemax={budget.budget}
        aria-valuenow={Math.min(budget.spent, budget.budget)}
        aria-valuetext={label}
        className="relative h-2.5 w-full overflow-hidden rounded-full bg-bg-elevated"
      >
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${used}%` }} />
        {budget.reserve_pct > 0 && (
          <span aria-hidden data-testid="reserve-mark" className="absolute inset-y-0 w-px bg-text-primary/60" style={{ left: `${reserveStart}%` }} />
        )}
      </div>
      <div className="mt-1.5 flex justify-between gap-3 text-xs text-text-secondary">
        <span>{formatCredits(budget.spent)} used</span>
        {budget.reserve_pct > 0 && <span>reserve {formatCredits(budget.reserve)} for live calls</span>}
      </div>
    </div>
  );
}

function Credits({ s }: { s: UsageSummary }) {
  const b = s.budget;
  return (
    <>
      <BudgetBar budget={b} />
      <p className="mt-2">
        <strong className="font-semibold">{formatCredits(b.left)}</strong> of {formatCredits(b.budget)} credits left
        {b.left_rupees != null && <> (about {formatRupees(b.left_rupees)})</>}
        {" · "}
        {b.reserve_reached ? "only the reserve is left: desk work waits, live calls continue" : `${formatCredits(b.left_for_desk)} before the reserve`}
      </p>
    </>
  );
}

function when(ts: number | null): string {
  if (!ts) return "";
  return formatDateTime(ts * 1000);
}

/** Settings: which Gnani model answers on each lane and its state, the credit budget, latency, cache and the busy-or-down history.
 *  Every figure is estimated from the rates the server lists; the card says so. */
export function SpeechModelsCard({ days = 7 }: { days?: number }) {
  const { data, error, isLoading } = useUsageSummary(days);
  const kinds = data ? (["stt", "tts"] as const).filter((k) => data.by_kind[k]) : [];
  return (
    <Card aria-labelledby="speech-models" className="space-y-1">
      <div className="border-b border-border pb-4">
        <h2 id="speech-models" className="font-display text-lg font-semibold text-text-primary">Speech models</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Gnani Prisma hears and Gnani Timbre speaks, one request at a time. A live call always goes first; desk work and tests queue behind it.
        </p>
      </div>
      {isLoading ? (
        <Skeleton className="my-3 h-24 w-full" />
      ) : error || !data ? (
        <p role="status" className="py-3 text-sm text-text-secondary">The usage figures are not available yet. They appear once the server has handled a request.</p>
      ) : (
        <dl className="divide-y divide-border">
          <Row label="Live calls hear with">
            <ProviderValue p={data.providers.live.stt} />
          </Row>
          <Row label="Live calls speak with">
            <ProviderValue p={data.providers.live.tts} />
          </Row>
          <Row label="Desk and previews hear with">
            <ProviderValue p={data.providers.desk.stt} />
          </Row>
          <Row label="Desk and previews speak with">
            <ProviderValue p={data.providers.desk.tts} />
          </Row>
          <Row label="Credits" note={data.rates_verified ? "Estimated from measured rates." : "Estimated from default rates that have not been measured on this account yet."}>
            <Credits s={data} />
          </Row>
          <Row label={`Last ${data.window_days} days`} note={`${formatCredits(data.totals.credits)} credits${data.totals.rupees != null ? ` (about ${formatRupees(data.totals.rupees)})` : ""} in ${data.totals.requests} requests.`}>
            {data.cost_per_booking != null ? (
              <>About <strong className="font-semibold">{data.cost_per_booking_rupees != null ? formatRupees(data.cost_per_booking_rupees) : `${formatCredits(data.cost_per_booking)} credits`}</strong> per booking</>
            ) : (
              "No bookings made on calls in this period yet"
            )}
          </Row>
          <Row label="Speed">
            {kinds.length === 0 ? (
              <span className="text-text-secondary">Nothing measured yet</span>
            ) : (
              <ul className="space-y-0.5">
                {kinds.map((k) => (
                  <li key={k}>
                    <span className="text-text-secondary">{KIND_LABEL[k]}:</span> {latencyLine(data.by_kind[k]) ?? "no timings yet"}
                  </li>
                ))}
              </ul>
            )}
          </Row>
          <Row label="Waiting for the slot" note="Gnani lets one request run at a time, so requests queue.">
            {data.queue_wait.p50_ms == null ? (
              <span className="text-text-secondary">No waiting measured yet</span>
            ) : (
              <>p50 {formatMs(data.queue_wait.p50_ms)} · p95 {formatMs(data.queue_wait.p95_ms)}</>
            )}
          </Row>
          <Row label="Answered from memory" note="The same words in the same voice are not sent twice, and cost nothing.">
            {formatPercent(data.totals.cache_hit_rate)} of requests
          </Row>
          <Row label="When Gnani was busy or down" note="A live caller hears a short held line and is offered a callback or a person. Other work waits and is retried.">
            {data.degraded.count === 0 ? (
              <span className="text-text-secondary">Never in the last {data.window_days} days</span>
            ) : (
              <>
                <p>
                  <strong className="font-semibold">{data.degraded.count}</strong> time{data.degraded.count === 1 ? "" : "s"} in the last {data.window_days} days
                  {data.degraded.hold_lines_played > 0 && <>, a held line was played {data.degraded.hold_lines_played} time{data.degraded.hold_lines_played === 1 ? "" : "s"}</>}
                </p>
                <ul className="mt-1.5 space-y-1" aria-label="Recent times Gnani could not answer">
                  {data.degraded.recent.slice(0, 5).map((f, i) => (
                    <li key={`${f.ts}-${i}`} className="text-xs text-text-secondary">
                      <span className="font-medium text-text-primary">{degradedLabel(f.reason, f.action)}</span>
                      {f.kind ? ` · ${KIND_LABEL[f.kind] ?? f.kind}` : ""}
                      {f.ts ? ` · ${when(f.ts)}` : ""}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Row>
        </dl>
      )}
    </Card>
  );
}
