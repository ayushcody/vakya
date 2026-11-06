"use client";

import { useMemo } from "react";
import { ErrorBoundary, PageHeader } from "@/components/ui";
import { WeekError, WeekLoading, WeekView } from "@/components/week/WeekView";
import { useBookings, useCalls } from "@/components/desk/calls";
import { formatDayRange } from "@/lib/formatDate";
import { useSchedules } from "@/lib/schedules";
import { buildWeek } from "@/lib/weekReport";
import { useNow } from "@/hooks/useNow";

/**
 * This week: the last seven days of the front desk, from what Vakya stored (calls, bookings, opening hours). Fourteen days
 * are fetched so each count can be set against the week before. Contract: docs/DESK_FEATURES.md.
 */
export default function ThisWeekPage() {
  const calls = useCalls(14, 200);
  const bookings = useBookings(14);
  const schedules = useSchedules();
  const now = useNow();

  const report = useMemo(
    () =>
      calls.data
        ? buildWeek({
            calls: calls.data.calls,
            bookings: bookings.data,
            schedules: schedules.data ?? [],
            timezone: calls.data.timezone,
            now: new Date(now),
            truncated: calls.data.truncated,
          })
        : null,
    [calls.data, bookings.data, schedules.data, now]
  );
  const today = report?.days[report.days.length - 1] ?? "";

  return (
    <ErrorBoundary>
      <PageHeader
        width="wide"
        title="This week"
        description={
          report
            ? `${formatDayRange(report.days[0], today)}. Every number is counted from the calls and bookings Vakya stored; practice calls are left out.`
            : "The last seven days of the front desk."
        }
      >
        {calls.error && !calls.data ? (
          <WeekError error={calls.error} onRetry={() => void calls.mutate()} />
        ) : !report ? (
          <WeekLoading />
        ) : (
          <WeekView report={report} today={today} />
        )}
      </PageHeader>
    </ErrorBoundary>
  );
}
