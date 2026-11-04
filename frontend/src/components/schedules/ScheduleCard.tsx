import Link from "next/link";
import { ArrowRight, BedDouble, CalendarClock, Plus } from "lucide-react";
import { accentColor, hoursInWords, kindLine, nextFreeWords, nowIn, type ScheduleWithSummary } from "@/lib/schedules";

/** One schedule: what it is, when it is open, how today looks and the next free time. The whole card opens the calendar. */
export function ScheduleCard({ schedule: s, deskName, now }: { schedule: ScheduleWithSummary; deskName?: string | null; now: number }) {
  const sum = s.summary;
  const today = nowIn(s.timezone, now).date;
  const Icon = s.kind === "nights" ? BedDouble : CalendarClock;
  const nights = s.kind === "nights";
  return (
    <Link
      href={`/desk/bookings/${encodeURIComponent(s.id)}`}
      className="group relative flex w-full min-w-0 flex-col overflow-hidden rounded-2xl border border-border bg-bg-surface transition-colors hover:border-border-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary"
    >
      <span aria-hidden className="h-1.5 w-full" style={{ background: accentColor(s.accent) }} />
      <div className="flex flex-1 flex-col gap-4 p-5">
        <div className="flex items-start gap-3">
          <span
            aria-hidden
            className="grid size-10 shrink-0 place-items-center rounded-xl"
            style={{ background: `color-mix(in srgb, ${accentColor(s.accent)} 16%, var(--color-bg-surface))`, color: accentColor(s.accent) }}
          >
            <Icon className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="truncate font-display text-xl leading-tight text-text-primary">{s.name}</h3>
            <p className="mt-0.5 text-sm text-text-secondary">
              {kindLine(s)}
              {deskName ? <> · {deskName}</> : null}
            </p>
          </div>
        </div>
        <p className="text-[15px] leading-snug text-text-primary">{hoursInWords(s.week, "short")}</p>
        <dl className="mt-auto grid grid-cols-2 gap-3 border-t border-border pt-4 text-sm">
          <div>
            <dt className="text-text-secondary">{nights ? "Rooms left tonight" : "Today"}</dt>
            <dd className="mt-0.5 font-semibold text-text-primary tabular-nums">
              {sum ? (nights ? `${sum.today_free}` : `${sum.today_free} free, ${sum.today_booked} booked`) : "..."}
            </dd>
          </div>
          <div>
            <dt className="text-text-secondary">Next free</dt>
            <dd className="mt-0.5 font-semibold text-text-primary">{sum ? nextFreeWords(sum.next_free, today) : "..."}</dd>
          </div>
        </dl>
        <span className="inline-flex items-center gap-1 text-sm font-medium text-accent-primary">
          Open calendar <ArrowRight aria-hidden className="size-3.5 transition-transform group-hover:translate-x-0.5" />
        </span>
      </div>
    </Link>
  );
}

export function NewScheduleCard({ agentId }: { agentId?: string | null }) {
  return (
    <Link
      href={`/desk/bookings/new${agentId ? `?agent=${encodeURIComponent(agentId)}` : ""}`}
      className="flex min-h-56 w-full min-w-0 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border-3 bg-transparent p-6 text-center transition-colors hover:bg-bg-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary"
    >
      <span aria-hidden className="grid size-11 place-items-center rounded-full bg-bg-elevated text-text-primary">
        <Plus className="size-5" />
      </span>
      <span className="font-display text-xl text-text-primary">New schedule</span>
      <span className="max-w-[16rem] text-sm text-text-secondary">Set the days, hours and slot length callers can book.</span>
    </Link>
  );
}
