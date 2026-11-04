"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { UserRound, X } from "lucide-react";
import { Button, EmptyState, ErrorBoundary, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import { useScheduleBookings } from "@/components/desk/calls";
import { errMessage } from "@/components/desk/format";
import { ListView } from "@/components/ui/ListView";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { FamilyCard } from "@/components/memory";
import { CallerCard } from "../calls/CallerCard";
import { CALLERS_KEY, type CallerProfile, type CallersResponse } from "../calls/callers";
import { callerColumns, callerKey } from "./CallersList";

function CallersContent() {
  const { data, error, isLoading, mutate } = useSWR<CallersResponse>(CALLERS_KEY, (p: string) => api.get<CallersResponse>(p), {
    revalidateOnFocus: true,
    shouldRetryOnError: false,
  });
  const focus = useSearchParams()?.get("caller") ?? null; // a link with ?caller=<caller_ref> (a booking's "who") opens that caller
  const bookings = useScheduleBookings(null);
  const callers = useMemo(() => (Array.isArray(data?.callers) ? data.callers : []), [data]);
  const booked = useMemo(() => {
    const n: Record<string, number> = {};
    for (const b of bookings.data) if (b.callerRef && b.status === "booked") n[b.callerRef] = (n[b.callerRef] ?? 0) + 1;
    return n;
  }, [bookings.data]);
  const columns = useMemo(() => callerColumns(booked), [booked]);

  const [selected, setSelected] = useState<string | null>(focus);
  const [open, setOpen] = useState<boolean>(!!focus);
  const [closedFocus, setClosedFocus] = useState(false);
  const chosen = callers.find((p) => callerKey(p) === selected) ?? null;
  const pane = open && !!chosen && !(closedFocus && selected === focus);
  const missing = !!focus && !!data && !isLoading && !callers.some((p) => callerKey(p) === focus);
  const close = useCallback(() => {
    setOpen(false);
    if (selected === focus) setClosedFocus(true);
    if (selected) requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(selected)}"]`)?.focus());
  }, [selected, focus]);

  useEffect(() => {
    if (!pane) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector("[role=dialog]")) close(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [pane, close]);

  // A link with ?caller=<ref>: bring that row into view once the list has loaded.
  const scrolled = useRef(false);
  useEffect(() => {
    if (scrolled.current || !focus || !callers.some((p) => callerKey(p) === focus)) return;
    scrolled.current = true;
    document.querySelector(`[data-row-id="${CSS.escape(focus)}"]`)?.scrollIntoView?.({ block: "center" });
  }, [callers, focus]);

  return (
    <PageHeader
      width="full"
      title="Callers"
      description="One number, the whole family: what the front desk remembers about each caller and the people they book for, so a son ringing for his mother is greeted by name, in their language, and asked whether it is for her again. Pick a caller to edit anything, add a note for the next call, or forget them and the next call starts fresh."
    >
      {error && <ErrorState title="Could not load the callers" message={errMessage(error, "Check that the API is running.")} onRetry={() => mutate()} />}
      {isLoading && (
        <div className="space-y-2" aria-busy="true" aria-label="Loading callers">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-11 w-full" />)}
        </div>
      )}
      {data && data.enabled === false && (
        <EmptyState
          icon={<UserRound />}
          title="Caller memory is switched off"
          description="The front desk treats every call as a first call. Turn caller memory on in the backend settings to remember returning callers."
          action={<Link href="/desk/settings"><Button size="sm" variant="outline">Open Settings</Button></Link>}
        />
      )}
      {data && data.enabled !== false && callers.length === 0 && (
        <EmptyState
          icon={<UserRound />}
          title="No callers remembered yet"
          description="After a phone call, the front desk keeps the caller's name, language and last booking, for a limited time. They appear here."
          action={<Link href="/desk/try"><Button size="sm">Try a call</Button></Link>}
        />
      )}
      {missing && (
        <p role="status" className="rounded-lg border border-border bg-bg-surface p-3 text-sm text-text-secondary">
          This caller is not remembered (no profile yet, it was forgotten, or it has expired).
        </p>
      )}
      {callers.length > 0 && (
        <div className={cn("grid items-start gap-4", pane && "lg:max-[1439px]:grid-cols-[minmax(0,1fr)_336px] min-[1440px]:grid-cols-[minmax(0,1fr)_420px]")}>
          <ListView<CallerProfile>
            label="Callers"
            rows={callers}
            columns={columns}
            getRowId={callerKey}
            selectedId={selected}
            onSelect={(p) => setSelected(callerKey(p))}
            onOpen={(p) => { setSelected(callerKey(p)); setOpen(true); }}
            defaultSort={{ id: "last", dir: "desc" }}
            storageKey="callers"
            footer={<p className="mt-2 text-xs text-text-secondary">{callers.length} {callers.length === 1 ? "caller" : "callers"} remembered{typeof data?.total === "number" && data.total > callers.length ? ` of ${data.total}` : ""}.</p>}
          />
          {pane && chosen && (
            <aside
              aria-label="Caller details"
              className="fixed inset-0 z-50 overflow-y-auto bg-bg-surface p-3 lg:sticky lg:inset-auto lg:top-4 lg:z-0 lg:max-h-[calc(100vh-2rem)] lg:bg-transparent lg:p-0"
            >
              <div className="mb-2 flex items-center justify-between gap-2 lg:mb-1.5 lg:justify-end">
                <Button variant="ghost" size="sm" onClick={close} leftIcon={<X className="size-4" />}>Close</Button>
              </div>
              <CallerCard
                key={chosen.id}
                profile={chosen}
                manage
                highlighted={!!focus && callerKey(chosen) === focus}
                onForgotten={async () => { setOpen(false); setSelected(null); await mutate(); }}
                onChanged={() => mutate()}
              />
              <FamilyCard key={`family-${chosen.id}`} className="mt-3" profile={chosen} onChanged={() => mutate()} />
            </aside>
          )}
        </div>
      )}
    </PageHeader>
  );
}

export default function CallersPage() {
  return (
    <ErrorBoundary>
      {/* useSearchParams needs a Suspense boundary for static rendering */}
      <Suspense fallback={null}>
        <CallersContent />
      </Suspense>
    </ErrorBoundary>
  );
}
