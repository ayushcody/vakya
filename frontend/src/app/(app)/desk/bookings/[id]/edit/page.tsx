"use client";

import { Suspense } from "react";
import { useParams } from "next/navigation";
import { CalendarX } from "lucide-react";
import { EmptyState, ErrorBoundary, ErrorState, Skeleton } from "@/components/ui";
import { PageShell } from "@/components/ui/PageHeader";
import { LinkButton } from "@/components/schedules/LinkButton";
import { ScheduleForm } from "@/components/schedules/ScheduleForm";
import { plainError, useSchedule } from "@/lib/schedules";

function EditSchedule({ id }: { id: string }) {
  const { schedule, error, isLoading, notFound, mutate } = useSchedule(id);
  if (isLoading || (!schedule && !error && !notFound)) {
    return (
      <PageShell width="wide">
        <Skeleton className="h-96 w-full rounded-2xl" />
      </PageShell>
    );
  }
  if (error && !schedule) {
    return (
      <PageShell width="default">
        <ErrorState title="Could not load this schedule" message={plainError(error, "Check that the server is running.")} onRetry={() => void mutate()} />
      </PageShell>
    );
  }
  if (!schedule) {
    return (
      <PageShell width="default">
        <EmptyState icon={<CalendarX />} title="This schedule is not here any more" description="It may have been deleted." action={<LinkButton href="/desk/schedules">Back to schedules</LinkButton>} />
      </PageShell>
    );
  }
  // Keyed by id and last save so the form starts from what is stored.
  return <ScheduleForm key={`${schedule.id}-${schedule.updated_at ?? ""}`} schedule={schedule} />;
}

export default function EditSchedulePage() {
  const { id } = useParams<{ id: string }>();
  return (
    <ErrorBoundary>
      <Suspense fallback={null}>
        <EditSchedule id={decodeURIComponent(id)} />
      </Suspense>
    </ErrorBoundary>
  );
}
