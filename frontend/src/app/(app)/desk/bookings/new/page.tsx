"use client";

import { Suspense } from "react";
import { ErrorBoundary, Skeleton } from "@/components/ui";
import { PageShell } from "@/components/ui/PageHeader";
import { ScheduleForm } from "@/components/schedules/ScheduleForm";

export default function NewSchedulePage() {
  return (
    <ErrorBoundary>
      <Suspense
        fallback={
          <PageShell width="wide">
            <Skeleton className="h-96 w-full rounded-2xl" />
          </PageShell>
        }
      >
        <ScheduleForm />
      </Suspense>
    </ErrorBoundary>
  );
}
