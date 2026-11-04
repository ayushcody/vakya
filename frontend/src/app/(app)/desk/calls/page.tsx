"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { PhoneCall, Search } from "lucide-react";
import { Button, EmptyState, ErrorBoundary, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import { ListView } from "@/components/ui/ListView";
import { useCallUsage } from "@/components/usage/useUsage";
import { useAgents } from "@/hooks/useApi";
import { useNow } from "@/hooks/useNow";
import { api } from "@/lib/api";
import { errMessage } from "@/components/desk/format";
import { cn } from "@/lib/utils";
import { CallDetails, callColumns, searchText, type CallViewContext } from "./CallRow";
import { LiveCalls, useLiveCalls } from "./LiveCalls";
import { TimeRangeBar } from "./TimeRange";
import { useKnownCallers } from "./callers";
import {
  CHANNEL_KIND_LABEL, CHANNEL_KIND_ORDER, DEFAULT_TIMEZONE, OUTCOME_LABEL, OUTCOME_ORDER, channelKind, dayHeading, dayKey, parseRange, rangeApiQuery, rangeQuery,
  type ChannelKind, type TimeRange,
} from "./format";
import type { CallOutcomeKey, CallSummary, CallsResponse } from "./types";

const LIMIT = 200;
const selectCls = "h-9 min-w-0 rounded-lg border border-border bg-bg-surface px-2.5 text-sm text-text-primary";
const primaryLink =
  "inline-flex h-10 items-center justify-center rounded-full bg-brand px-4 text-sm font-medium text-[var(--color-on-accent)] transition-opacity hover:opacity-90";

/** Puts the range and the open call in the address (without a new history entry) so a link reproduces the view. */
function writeUrl(range: TimeRange, call: string | null) {
  try {
    const q = [rangeQuery(range), call ? `call=${encodeURIComponent(call)}` : ""].filter(Boolean).join("&");
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${q ? `?${q}` : ""}`);
  } catch {
    /* a browser without history access just keeps the state in the page */
  }
}

function CallsContent() {
  const params = useSearchParams();
  const [range, setRange] = useState<TimeRange>(() => parseRange((n) => params?.get(n)));
  const [selected, setSelected] = useState<string | null>(() => params?.get("call") ?? null);   // a link with ?call=<id> opens that conversation
  const [open, setOpen] = useState<boolean>(() => !!params?.get("call"));
  const [opened, setOpened] = useState(0);
  const [agent, setAgent] = useState("");
  const [kind, setKind] = useState<"" | ChannelKind>("");
  const [outcome, setOutcome] = useState<"" | CallOutcomeKey>("");
  const [query, setQuery] = useState("");
  const [hideEmpty, setHideEmpty] = useState(true);
  const [listenTo, setListenTo] = useState<{ id: string; n: number } | null>(null);
  const agents = useAgents();
  const now = useNow();

  const bad = range.kind === "custom" && !!range.from && !!range.to && range.from > range.to;
  const key = bad ? null : `/api/calls?${rangeApiQuery(range)}&limit=${LIMIT}${hideEmpty ? "&hide_empty=true" : ""}${agent ? `&agent_id=${encodeURIComponent(agent)}` : ""}`;
  const { data, error, isLoading, mutate } = useSWR<CallsResponse>(key, (p: string) => api.get<CallsResponse>(p), {
    refreshInterval: 30000, // new conversations appear without a reload (paused while the tab is hidden)
    revalidateOnFocus: true,
    shouldRetryOnError: false,
    keepPreviousData: true,
  });

  const tz = data?.timezone ?? DEFAULT_TIMEZONE;
  const today = dayKey(now, tz) ?? "";
  const { byCall, refresh } = useKnownCallers();
  const usage = useCallUsage();
  const live = useLiveCalls();
  const liveIds = useMemo(() => new Set((live.data?.calls ?? []).map((c) => c.conversation_id)), [live.data]);
  const ctx = useMemo<CallViewContext>(() => ({ tz, today, known: byCall, usage: usage.data?.calls ?? {}, live: liveIds }), [tz, today, byCall, usage.data, liveIds]);
  const columns = useMemo(() => callColumns(ctx), [ctx]);

  const calls = useMemo(() => data?.calls ?? [], [data]);
  const needle = query.trim().toLowerCase();
  const matching = useMemo(
    () => calls.filter((c) => (!kind || channelKind(c) === kind) && (!needle || searchText(c, ctx).includes(needle))),
    [calls, kind, needle, ctx]
  );
  const counts = useMemo(() => {
    const n: Partial<Record<CallOutcomeKey, number>> = {};
    for (const c of matching) n[c.outcome.key] = (n[c.outcome.key] ?? 0) + 1;
    return n;
  }, [matching]);
  const shown = useMemo(() => matching.filter((c) => !outcome || c.outcome.key === outcome), [matching, outcome]);
  const filtered = !!(agent || kind || outcome || needle);
  const clear = () => { setAgent(""); setKind(""); setOutcome(""); setQuery(""); };

  const chooseRange = (r: TimeRange) => { setRange(r); writeUrl(r, open ? selected : null); };
  const select = useCallback((c: CallSummary) => { setSelected(c.id); if (open) writeUrl(range, c.id); }, [open, range]);
  const openCall = useCallback((c: CallSummary) => { setSelected(c.id); setOpen(true); setOpened((n) => n + 1); writeUrl(range, c.id); }, [range]);
  const close = useCallback(() => {
    setOpen(false);
    writeUrl(range, null);
    if (selected) requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(selected)}"]`)?.focus());
  }, [range, selected]);
  const listen = (id: string) => { setListenTo((l) => ({ id, n: (l?.n ?? 0) + 1 })); document.getElementById("live-now")?.scrollIntoView?.({ block: "nearest" }); };

  // A link with ?call=<id>: bring that row into view once the list has loaded.
  const scrolled = useRef(false);
  useEffect(() => {
    if (scrolled.current || !selected || !calls.some((c) => c.id === selected)) return;
    scrolled.current = true;
    document.querySelector(`[data-row-id="${CSS.escape(selected)}"]`)?.scrollIntoView?.({ block: "center" });
  }, [calls, selected]);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector("[role=dialog]")) close(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open, close]);

  const summary = data && !error ? `${calls.length} ${calls.length === 1 ? "call" : "calls"}` : undefined;
  const summaryRow = selected ? calls.find((c) => c.id === selected) ?? null : null;
  const hidden = data?.empty_hidden ?? 0;
  const none = !!data && calls.length === 0 && !agent;
  const noneTitle = range.kind === "preset" && range.key === "today" ? "No calls today yet" : range.kind === "preset" && range.key === "yesterday" ? "No calls yesterday" : hidden > 0 ? "No calls with anything said" : "No conversations yet";

  const toolbar = (
    <>
      <label className="relative block w-full sm:w-64">
        <span className="sr-only">Search calls</span>
        <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-text-secondary" />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search caller, number, booking"
          className="h-9 w-full min-w-0 rounded-lg border border-border bg-bg-surface pl-8 pr-2.5 text-sm text-text-primary placeholder:text-text-secondary"
        />
      </label>
      <label className="flex items-center gap-1.5 text-sm text-text-secondary">
        <span className="sr-only sm:not-sr-only">Front desk</span>
        <select aria-label="Front desk" value={agent} onChange={(e) => setAgent(e.target.value)} className={selectCls}>
          <option value="">All front desks</option>
          {(agents.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </label>
      <label className="flex items-center gap-1.5 text-sm text-text-secondary">
        <span className="sr-only sm:not-sr-only">Where</span>
        <select aria-label="Where" value={kind} onChange={(e) => setKind(e.target.value as "" | ChannelKind)} className={selectCls}>
          <option value="">Everywhere</option>
          {CHANNEL_KIND_ORDER.map((c) => <option key={c} value={c}>{CHANNEL_KIND_LABEL[c]}</option>)}
        </select>
      </label>
      <label className="flex min-h-9 cursor-pointer items-center gap-2 text-sm text-text-primary">
        <input type="checkbox" checked={hideEmpty} onChange={(e) => setHideEmpty(e.target.checked)} className="size-4 accent-[var(--color-accent-primary)]" />
        Hide empty calls
      </label>
      {filtered && <Button variant="ghost" size="sm" onClick={clear}>Clear filters</Button>}
    </>
  );

  const withPane = open && !!selected;
  return (
    <PageHeader
      width="full"
      title="Calls"
      description="Every call your front desk took: on the phone, on a web link or from this app. Pick a row to read what was said and listen in while it is live."
    >
      <LiveCalls listenTo={listenTo} />
      <TimeRangeBar value={range} today={today} onChange={chooseRange} summary={summary} />
      {error && !data && <ErrorState title="Could not load the conversations" message={errMessage(error, "Check that the API is running.")} onRetry={() => mutate()} />}
      {isLoading && !data && (
        <div className="space-y-2" aria-busy="true" aria-label="Loading conversations">
          {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-11 w-full" />)}
        </div>
      )}

      {none && (
        <EmptyState
          icon={<PhoneCall />}
          title={noneTitle}
          description={hidden > 0 ? "Calls that connected and dropped without a word are hidden. Show them to see every ring." : "When someone rings your front desk, or when you try a call from this app, it appears on this page: when it happened, what was said, and whether it ended in a booking."}
          action={
            <div className="flex flex-wrap justify-center gap-2">
              {hidden > 0 && <Button variant="outline" onClick={() => setHideEmpty(false)}>Show empty calls</Button>}
              {range.kind === "preset" && (range.key === "today" || range.key === "yesterday") && (
                <Button variant="outline" onClick={() => chooseRange({ kind: "preset", key: "7d" })}>Show the last 7 days</Button>
              )}
              <Link href="/desk/try" className={primaryLink}>Try a call</Link>
              <Link href="/desk/receptionist" className="inline-flex h-10 items-center justify-center rounded-full border border-border bg-bg-surface px-4 text-sm font-medium text-text-primary hover:border-text-primary">
                Set up a front desk
              </Link>
            </div>
          }
        />
      )}

      {data && !none && (
        <div className={cn("grid items-start gap-4", withPane && "lg:max-[1439px]:grid-cols-[minmax(0,1fr)_336px] min-[1440px]:grid-cols-[minmax(0,1fr)_392px]")}>
          <div className="min-w-0 space-y-3">
            <div role="group" aria-label="Filter by what happened" className="flex flex-wrap gap-1.5">
              {([["", "All", matching.length] as const, ...OUTCOME_ORDER.filter((k) => counts[k]).map((k) => [k, OUTCOME_LABEL[k], counts[k] ?? 0] as const)]).map(([k, label, n]) => (
                <button
                  key={k || "all"}
                  type="button"
                  aria-pressed={outcome === k}
                  onClick={() => setOutcome(k as "" | CallOutcomeKey)}
                  className={cn(
                    "min-h-8 rounded-full border px-3 text-sm font-medium transition-colors",
                    outcome === k ? "border-accent-primary/50 bg-accent-primary/15 text-text-primary" : "border-border text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
                  )}
                >
                  {label} <span className="tabular-nums text-text-secondary">{n}</span>
                </button>
              ))}
            </div>

            <ListView<CallSummary>
              label="Calls"
              rows={shown}
              columns={columns}
              getRowId={(c) => c.id}
              selectedId={selected}
              onSelect={select}
              onOpen={openCall}
              defaultSort={{ id: "time", dir: "desc" }}
              groupBy={(c) => {
                const d = dayKey(c.when, tz);
                return d ? { key: d, label: dayHeading(d, today) } : { key: "unknown", label: "Unknown time" };
              }}
              pinned={(c) => ctx.live.has(c.id)}
              pinnedLabel="Live now"
              storageKey="calls"
              toolbar={toolbar}
              empty={
                <div className="px-6 py-12 text-center">
                  <p className="text-[15px] font-medium text-text-primary">Nothing matches these filters</p>
                  <p className="mt-1 text-sm text-text-secondary">Try another place, outcome or word.</p>
                  <Button variant="outline" className="mt-4" onClick={clear}>Clear filters</Button>
                </div>
              }
              footer={
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-text-secondary">
                  <span>
                    {data.truncated || calls.length >= LIMIT ? `Showing the ${LIMIT} most recent calls.` : `${shown.length} of ${calls.length} ${calls.length === 1 ? "call" : "calls"}`}
                    {hidden > 0 && hideEmpty ? ` · ${hidden} empty ${hidden === 1 ? "call" : "calls"} hidden` : ""}
                  </span>
                  {hidden > 0 && hideEmpty && <button type="button" className="font-medium text-accent-primary hover:underline" onClick={() => setHideEmpty(false)}>Show empty calls</button>}
                </div>
              }
            />
          </div>

          {withPane && selected && (
            <aside
              aria-label="Call details"
              className="fixed inset-0 z-50 overflow-y-auto bg-bg-surface lg:sticky lg:inset-auto lg:top-4 lg:z-0 lg:max-h-[calc(100vh-2rem)] lg:rounded-xl lg:border lg:border-border"
            >
              <CallDetails id={selected} summary={summaryRow} ctx={ctx} onClose={close} onListen={listen} onForgotten={refresh} focusSignal={opened} />
            </aside>
          )}
        </div>
      )}
    </PageHeader>
  );
}

export default function CallsPage() {
  return (
    <ErrorBoundary>
      {/* useSearchParams needs a Suspense boundary for static rendering */}
      <Suspense fallback={null}>
        <CallsContent />
      </Suspense>
    </ErrorBoundary>
  );
}
