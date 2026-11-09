"use client";

import { useEffect, useState } from "react";
import { Eye } from "lucide-react";
import { readonlyDesk } from "@/lib/readonlyDesk";

/** On the public demo desk: a quiet pill saying this is sample data and that calls and edits are off. */
export function ReadonlyBanner() {
  const [on, setOn] = useState(false);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- the host is only known in the browser
  useEffect(() => setOn(readonlyDesk()), []);
  if (!on) return null;
  return (
    <div role="status" className="pointer-events-none fixed inset-x-0 bottom-20 z-40 flex justify-center px-4 md:bottom-4">
      <span className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-border bg-bg-surface/95 px-4 py-2 text-xs text-text-secondary shadow-lg backdrop-blur">
        <Eye className="size-3.5 text-[var(--area-accent,#6D5BD0)]" aria-hidden />
        Read-only demo with sample data. Calls, bookings and edits are switched off here.
      </span>
    </div>
  );
}
