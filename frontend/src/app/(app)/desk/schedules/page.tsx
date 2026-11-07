"use client";

import { Suspense } from "react";
import { ErrorBoundary } from "@/components/ui";
import { BookingsBoard } from "@/components/desk/BookingsBoard";

export default function SchedulesPage() {
  return (
    <ErrorBoundary>
      {/* useSearchParams needs a Suspense boundary for static rendering */}
      <Suspense fallback={null}>
        <BookingsBoard view="schedules" />
      </Suspense>
    </ErrorBoundary>
  );
}
