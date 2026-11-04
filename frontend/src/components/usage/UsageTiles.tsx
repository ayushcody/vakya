"use client";

import { Fragment } from "react";
import { cn } from "@/lib/utils";
import { formatCredits, formatRupees } from "./format";
import { useUsageSummary } from "./useUsage";

/** A line of the last days' credits. Drawn as one polyline; the figures are in the label for a screen reader. */
export function Sparkline({ values, label, className }: { values: number[]; label: string; className?: string }) {
  const w = 160;
  const h = 40;
  const max = Math.max(...values, 0);
  const step = values.length > 1 ? w / (values.length - 1) : 0;
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(max > 0 ? h - 3 - (v / max) * (h - 6) : h - 3).toFixed(1)}`);
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn("h-10 w-full overflow-visible", className)}>
      <title>{label}</title>
      {values.length > 1 && <polyline points={pts.join(" ")} fill="none" stroke="var(--color-accent-primary)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
      {pts.length > 0 && (
        // a zero-length line with a round cap draws a true circle even though the viewBox is stretched to the tile
        <line x1={pts[pts.length - 1].split(",")[0]} y1={pts[pts.length - 1].split(",")[1]} x2={Number(pts[pts.length - 1].split(",")[0]) + 0.01} y2={pts[pts.length - 1].split(",")[1]} stroke="var(--color-accent-primary)" strokeWidth="6" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      )}
    </svg>
  );
}

const tile = "rounded-2xl border border-border bg-bg-surface p-5";
const eyebrow = "text-xs font-semibold uppercase tracking-[1px] text-text-secondary";

/** Today: what a booking costs in Gnani credits, a week of credits as a sparkline, and how often Gnani was busy or down.
 *  Every figure is an estimate; the tiles say so in their hints. */
export function UsageTiles({ days = 7 }: { days?: number }) {
  const { data, isLoading } = useUsageSummary(days);
  const note = data ? (data.rates_verified ? "estimated from measured rates" : "estimated from default rates, not yet measured") : "estimated";
  const per = data?.cost_per_booking_rupees != null ? formatRupees(data.cost_per_booking_rupees) : data?.cost_per_booking != null ? formatCredits(data.cost_per_booking) : "—";
  const unit = data?.cost_per_booking_rupees == null && data?.cost_per_booking != null ? "credits" : "";
  const series = (data?.daily ?? []).map((d) => d.credits);
  const total = data?.totals.credits ?? 0;
  return (
    <Fragment>
      <div className={tile} data-testid="cost-per-booking">
        <p className={eyebrow}>Cost per booking</p>
        <p className="mt-2 font-display text-[2.25rem] leading-none text-text-primary tabular-nums">
          {isLoading ? "…" : per}
          {unit && <span className="ml-1.5 font-sans text-sm text-text-secondary">{unit}</span>}
        </p>
        <p className="mt-1.5 text-sm text-text-secondary">
          {data && data.cost_per_booking != null
            ? `over ${data.bookings} booking${data.bookings === 1 ? "" : "s"} in ${data.window_days} days, ${note}`
            : data
              ? `no bookings on calls in ${data.window_days} days yet`
              : "not available yet"}
        </p>
      </div>
      <div className={cn(tile, "sm:order-first sm:col-span-2 lg:order-none")} data-testid="usage-sparkline">
        <p className={eyebrow}>Speech credits, last {days} days</p>
        <div className="mt-2 flex items-end gap-4">
          <p className="font-display text-[2.25rem] leading-none text-text-primary tabular-nums">{data ? formatCredits(total) : "—"}</p>
          <div className="min-w-0 flex-1">
            {series.length > 0 && <Sparkline values={series} label={`Credits per day, last ${days} days: ${series.map((v) => formatCredits(v)).join(", ")}`} />}
          </div>
        </div>
        <p className="mt-1.5 text-sm text-text-secondary">
          {data ? `${data.totals.requests} requests, ${note}` : "not available yet"}
        </p>
      </div>
      <div className={tile} data-testid="degraded-count">
        <p className={eyebrow}>Gnani busy or down</p>
        <p className="mt-2 font-display text-[2.25rem] leading-none text-text-primary tabular-nums">{data ? data.degraded.count : "—"}</p>
        <p className="mt-1.5 text-sm text-text-secondary">
          {data ? (data.degraded.count === 0 ? `times in ${data.window_days} days` : "times: callers were held or offered a callback") : "not available yet"}
        </p>
      </div>
    </Fragment>
  );
}
