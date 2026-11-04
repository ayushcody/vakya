"use client";

import { useParams } from "next/navigation";
import { ErrorBoundary } from "@/components/ui";
import { ScheduleView } from "@/components/schedules/ScheduleView";

export default function SchedulePage() {
  const { id } = useParams<{ id: string }>();
  return (
    <ErrorBoundary>
      <ScheduleView id={decodeURIComponent(id)} />
    </ErrorBoundary>
  );
}
