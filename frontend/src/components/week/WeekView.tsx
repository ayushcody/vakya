"use client";

import Link from "next/link";
import { ArrowRight, CalendarRange, PhoneCall } from "lucide-react";
import { Badge, Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { OUTCOME_LABEL, OUTCOME_TONE, callerLabel, dayWords, whenLabel } from "@/app/(app)/desk/calls/format";
import type { CallOutcomeKey } from "@/app/(app)/desk/calls/types";
import { errMessage } from "@/components/desk/format";
import { hourLabel, versusLastWeek, type HeatCell, type WeekReport } from "@/lib/weekReport";
import { cn } from "@/lib/utils";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The week in one sentence, the way the owner would say it. Numbers carry the accent; nothing else does. */
function Lead({ r }: { r: WeekReport }) {
  const num = (n: number) => <span className="text-accent-primary tabular-nums">{n}</span>;
  const callsVs = versusLastWeek(r.calls, r.previousCalls);
  const bookedVs = versusLastWeek(r.booked, r.previousBooked);
  return (
    <section aria-label="The week in one sentence" className="max-w-3xl">
      <p className="font-display text-[clamp(1.5rem,2.4vw,2.125rem)] leading-[1.22] tracking-[-0.005em] text-text-primary [text-wrap:balance]">
        In the last seven days Vakya answered {num(r.calls)} {r.calls === 1 ? "call" : "calls"}
        {r.afterHours != null && r.afterHours > 0 && (
          <>, {num(r.afterHours)} of {r.afterHours === 1 ? "it" : "them"} while you were closed,</>
        )}{" "}
        {r.booked > 0 ? (
          <>and booked {num(r.booked)} {r.booked === 1 ? "appointment" : "appointments"}.</>
        ) : (
          <>and booked no appointments.</>
        )}
      </p>
      <p className="mt-3 text-[15px] leading-relaxed text-text-secondary">
        {[
          callsVs && `Calls ${callsVs}.`,
          bookedVs && `Bookings ${bookedVs}.`,
          r.minutes != null && `${plural(r.minutes, "minute")} on the phone that nobody at the desk had to spend.`,
          r.afterHours == null && "Set opening hours on a schedule to see the calls that came while you were closed.",
        ]
          .filter(Boolean)
          .join(" ")}
      </p>
    </section>
  );
}

/** Fill of one heatmap cell: the accent, stronger with more calls. */
function cellStyle(c: HeatCell, max: number): React.CSSProperties | undefined {
  if (!c.count || !max) return undefined;
  const pct = Math.round(22 + (78 * c.count) / max);
  return { backgroundColor: `color-mix(in srgb, var(--color-accent-primary) ${pct}%, transparent)` };
}

/** Closed hours: a fine hatch, kept under a cell's fill, so a violet cell on a hatch reads as "a call while closed". */
const HATCH = "bg-[repeating-linear-gradient(135deg,var(--color-border-3)_0_1px,transparent_1px_6px)]";

function Heatmap({ r, today }: { r: WeekReport; today: string }) {
  const hours = Array.from({ length: r.lastHour - r.firstHour + 1 }, (_, i) => r.firstHour + i);
  const hasHours = r.cells.some((d) => d.some((c) => c.open != null));
  return (
    <section aria-labelledby="week-heat-title" className="rounded-2xl border border-border bg-bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="week-heat-title" className="text-sm font-semibold text-text-primary">When people call</h2>
        {r.busiest && (
          <p className="text-sm text-text-secondary">
            Busiest: <span className="font-medium text-text-primary">{dayWords(r.busiest.day)}, {hourLabel(r.busiest.hour)}</span> ({plural(r.busiest.count, "call")})
          </p>
        )}
      </div>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[19rem] table-fixed border-separate border-spacing-[3px]">
          <caption className="sr-only">Calls in each hour of the last seven days{hasHours ? ", with the hours you were closed marked" : ""}.</caption>
          <thead>
            <tr>
              <th scope="col" className="w-12"><span className="sr-only">Hour</span></th>
              {r.days.map((d) => {
                const [wd, dn] = dayWords(d).split(" ");
                const isToday = d === today;
                return (
                  <th key={d} scope="col" className={cn("pb-1 text-center text-[11px] font-medium leading-tight", isToday ? "text-text-primary" : "text-text-secondary")}>
                    <span className="block">{isToday ? "Today" : wd}</span>
                    <span className="block tabular-nums">{dn}</span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {hours.map((h, row) => (
              <tr key={h}>
                <th scope="row" className="pr-1 text-right text-[11px] font-normal tabular-nums text-text-secondary">{hourLabel(h)}</th>
                {r.days.map((d, col) => {
                  const c = r.cells[col][row];
                  const strong = r.maxCell > 0 && c.count / r.maxCell > 0.5;
                  const label = `${dayWords(d)}, ${hourLabel(h)}: ${plural(c.count, "call")}${c.open === false ? ", closed" : ""}`;
                  return (
                    <td key={d} className="p-0">
                      <div
                        role="img"
                        aria-label={label}
                        title={label}
                        style={cellStyle(c, r.maxCell)}
                        className={cn(
                          "grid h-6 place-items-center rounded-[5px] text-[11px] font-medium tabular-nums",
                          c.count ? (strong ? "text-[var(--color-on-accent)]" : "text-text-primary") : "bg-bg-base",
                          c.open === false && HATCH
                        )}
                      >
                        {c.count > 0 ? c.count : ""}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-text-secondary" aria-hidden>
        <span className="inline-flex items-center gap-2">
          <span className="h-3 w-3 rounded-[3px]" style={{ backgroundColor: "color-mix(in srgb, var(--color-accent-primary) 30%, transparent)" }} />
          <span className="h-3 w-3 rounded-[3px]" style={{ backgroundColor: "color-mix(in srgb, var(--color-accent-primary) 65%, transparent)" }} />
          <span className="h-3 w-3 rounded-[3px] bg-accent-primary" />
          Fewer to more calls
        </span>
        {hasHours && (
          <span className="inline-flex items-center gap-2">
            <span className={cn("h-3 w-3 rounded-[3px] bg-bg-base", HATCH)} />
            Closed
          </span>
        )}
      </div>
    </section>
  );
}

const OUTCOME_BAR: Partial<Record<CallOutcomeKey, string>> = {
  booked: "bg-accent-primary",
  answered: "bg-violet-400",
  couldnt_answer: "bg-accent-error",
  handed_off: "bg-accent-warning",
  needs_approval: "bg-accent-warning",
};

function Outcomes({ r }: { r: WeekReport }) {
  const max = Math.max(1, ...r.outcomes.map((o) => o.count));
  return (
    <section aria-labelledby="week-outcomes-title" className="rounded-2xl border border-border bg-bg-surface p-4 sm:p-5">
      <h2 id="week-outcomes-title" className="text-sm font-semibold text-text-primary">How the calls ended</h2>
      <ul className="mt-4 space-y-3">
        {r.outcomes.map((o) => (
          <li key={o.key}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="text-text-primary">{o.label}</span>
              <span className="tabular-nums text-text-secondary">
                {o.count}
                <span className="sr-only"> of {r.calls} calls</span>
              </span>
            </div>
            <div className="mt-1.5 h-1.5 rounded-full bg-bg-base" aria-hidden>
              <div className={cn("h-full rounded-full", OUTCOME_BAR[o.key] ?? "bg-border-3")} style={{ width: `${(o.count / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

const LANGUAGE_FILL = ["bg-accent-primary", "bg-orange-600", "bg-blue-700", "bg-violet-400", "bg-orange-400"];

function Languages({ r }: { r: WeekReport }) {
  const total = r.languages.reduce((s, l) => s + l.count, 0) || 1;
  const fill = (code: string, i: number) => (code === "" ? "bg-border-3" : LANGUAGE_FILL[i % LANGUAGE_FILL.length]);
  return (
    <section aria-labelledby="week-lang-title" className="rounded-2xl border border-border bg-bg-surface p-4 sm:p-5">
      <h2 id="week-lang-title" className="text-sm font-semibold text-text-primary">Languages callers used</h2>
      <div className="mt-4 flex h-2.5 gap-0.5 overflow-hidden rounded-full" aria-hidden>
        {r.languages.map((l, i) => (
          <div key={l.code || "none"} className={fill(l.code, i)} style={{ width: `${(l.count / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-3 space-y-1.5">
        {r.languages.map((l, i) => (
          <li key={l.code || "none"} className="flex items-center gap-2 text-sm">
            <span aria-hidden className={cn("size-2.5 shrink-0 rounded-full", fill(l.code, i))} />
            <span className="flex-1 text-text-primary">{l.label}</span>
            <span className="tabular-nums text-text-secondary">
              {Math.round((l.count / total) * 100)}% <span className="sr-only">({plural(l.count, "call")})</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Attention({ r }: { r: WeekReport }) {
  return (
    <section aria-labelledby="week-attention-title">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 id="week-attention-title" className="text-sm font-semibold text-text-primary">Calls worth a look</h2>
        <Link href="/desk/calls" className="inline-flex min-h-8 items-center gap-1 text-sm font-medium text-accent-primary hover:underline">
          All calls <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </div>
      {r.attention.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-border px-5 py-6 text-sm text-text-secondary">
          Nothing slipped this week: every call ended with an answer or a booking.
        </p>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-bg-surface">
          {r.attention.map((c) => (
            <li key={c.id}>
              <Link href={`/desk/calls?call=${encodeURIComponent(c.id)}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 hover:bg-bg-elevated/60 sm:px-5">
                <span className="text-sm font-medium text-text-primary">{whenLabel(c.when)}</span>
                <Badge tone={OUTCOME_TONE[c.outcome.key]} dot>{OUTCOME_LABEL[c.outcome.key] ?? c.outcome.label}</Badge>
                <span className="min-w-0 flex-1 truncate text-sm text-text-secondary">
                  {callerLabel(c)}
                  {c.outcome.detail ? ` · ${c.outcome.detail}` : c.preview ? ` · “${c.preview}”` : ""}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function WeekLoading() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading the week">
      <Skeleton className="h-10 w-full max-w-2xl" />
      <Skeleton className="h-5 w-full max-w-md" />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <Skeleton className="h-96 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    </div>
  );
}

export function WeekError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return <ErrorState title="Could not load the week" message={errMessage(error, "Check that the API is running.")} onRetry={onRetry} />;
}

export function WeekView({ report, today }: { report: WeekReport; today: string }) {
  if (report.calls === 0) {
    return (
      <EmptyState
        icon={<CalendarRange />}
        title="No calls in the last seven days"
        description="When callers ring the front desk, this page shows when they called, how each call ended and the languages they spoke."
        action={
          <Link href="/desk/try">
            <Button size="sm" leftIcon={<PhoneCall className="size-4" aria-hidden />}>Try a call</Button>
          </Link>
        }
      />
    );
  }
  return (
    <div className="space-y-8">
      <Lead r={report} />
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <Heatmap r={report} today={today} />
        <div className="grid gap-4">
          <Outcomes r={report} />
          <Languages r={report} />
        </div>
      </div>
      <Attention r={report} />
      {report.truncated && (
        <p className="text-xs text-text-secondary">Only the newest 200 calls were counted, so the real numbers are a little higher.</p>
      )}
    </div>
  );
}
