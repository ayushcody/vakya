"use client";

import { Suspense } from "react";
import { ErrorBoundary } from "@/components/ui";
import { BookingsBoard } from "@/components/desk/BookingsBoard";

export default function BookingsPage() {
  return (
    <ErrorBoundary>
      {/* useSearchParams needs a Suspense boundary for static rendering */}
      <Suspense fallback={null}>
        <BookingsBoard view="bookings" />
      </Suspense>
    </ErrorBoundary>
  );
}
