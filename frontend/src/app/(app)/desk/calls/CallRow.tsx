"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, Globe, Headphones, MessageCircle, Mic, Phone, PhoneForwarded, Repeat, Wrench, X } from "lucide-react";
import { Badge, Button, Skeleton } from "@/components/ui";
import type { ListColumn } from "@/components/ui/ListView";
import { Misheard } from "@/components/hearing/Misheard";
import { CallUsageLine } from "@/components/usage/CallUsageLine";
import { formatCredits } from "@/components/usage/format";
import type { CallUsage } from "@/components/usage/types";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { CallerCard } from "./CallerCard";
import type { KnownCaller } from "./callers";
import {
  CHANNEL_KIND_LABEL, OUTCOME_LABEL, OUTCOME_ORDER, OUTCOME_TONE, bookingText, callerCell, channelKind, clockLabel, dayHeading, dayKey,
  deskCell, durationLabel, durationShort, languageLabel, type ChannelKind,
} from "./format";
import type { CallDetail, CallOutcomeKey, CallSummary } from "./types";

const CHANNEL_ICON: Record<ChannelKind, typeof Phone> = { phone: Phone, callback: PhoneForwarded, browser: Globe, practice: Mic, whatsapp: MessageCircle };

/** What the columns and the details pane need besides the call itself. */
export interface CallViewContext {
  tz: string;
  /** Today's day (YYYY-MM-DD) in `tz`. */
  today: string;
  /** Remembered callers by conversation id (the Callers page's profiles). */
  known: Record<string, KnownCaller>;
  /** What each call used (GET /api/usage/calls), by conversation id. */
  usage: Record<string, CallUsage | undefined>;
  /** Conversation ids of the calls happening now. */
  live: Set<string>;
}

const muted = "text-text-secondary";

export function OutcomePill({ outcome }: { outcome: { key: CallOutcomeKey; label: string } }) {
  return (
    <Badge tone={OUTCOME_TONE[outcome.key]} dot className="whitespace-nowrap rounded-full px-2.5 text-xs font-medium normal-case tracking-normal">
      {OUTCOME_LABEL[outcome.key] ?? outcome.label}
    </Badge>
  );
}

export function whenOf(c: CallSummary): number | null {
  const t = c.when ? new Date(c.when).getTime() : NaN;
  return Number.isNaN(t) ? null : t;
}

function callerOf(c: CallSummary, ctx: CallViewContext) {
  const p = ctx.known[c.id]?.profile;
  return callerCell(c, p?.display_name, p?.number_masked);
}

/** Everything a search box should match on one call: who, the number's last digits, the booking, what was said. */
export function searchText(c: CallSummary, ctx: CallViewContext): string {
  const who = callerOf(c, ctx);
  const p = ctx.known[c.id]?.profile;
  return [who.text, p?.number_masked, c.caller, c.booking?.id, c.booking?.with, c.preview, c.agent_name, CHANNEL_KIND_LABEL[channelKind(c)], OUTCOME_LABEL[c.outcome.key], c.outcome.detail]
    .filter(Boolean).join(" ").toLowerCase();
}

/** The columns of the Calls list. */
export function callColumns(ctx: CallViewContext): ListColumn<CallSummary>[] {
  const { tz, today } = ctx;
  return [
    {
      id: "time", header: "Time", width: ({ grouped }) => (grouped ? "80px" : "172px"), minWidth: ({ grouped }) => (grouped ? 80 : 172), required: true,
      stacked: { line: 1, slot: "start" },
      sortValue: whenOf,
      cell: (c, { grouped }) => {
        const live = ctx.live.has(c.id);
        const day = dayKey(c.when, tz);
        const clock = clockLabel(c.when, tz) || "Unknown time";
        return (
          <span className="flex min-w-0 items-center gap-2 whitespace-nowrap text-sm tabular-nums text-text-primary">
            {live && <span title="Live now" role="img" aria-label="Live now" className="size-2 shrink-0 rounded-full bg-accent-success motion-safe:animate-pulse" />}
            {grouped || !day ? clock : `${dayHeading(day, today)}, ${clock}`}
          </span>
        );
      },
    },
    {
      id: "caller", header: "Caller", width: "minmax(112px, 1fr)", minWidth: 112, required: true,
      stacked: { line: 1, slot: "fill" },
      sortValue: (c) => callerOf(c, ctx).text.toLowerCase(),
      cell: (c) => {
        const who = callerOf(c, ctx);
        const returning = ctx.known[c.id]?.returning;
        return (
          <span className="flex min-w-0 items-center gap-1.5">
            <span title={who.text} className={cn("truncate text-sm", who.muted ? muted : "font-medium text-text-primary")}>{who.text}</span>
            {returning && (
              <span data-testid="returning-caller" role="img" aria-label="Returning caller" title="Returning caller" className="shrink-0 text-accent-primary">
                <Repeat aria-hidden className="size-3.5" />
              </span>
            )}
          </span>
        );
      },
    },
    {
      id: "desk", header: "Front desk", width: "minmax(96px, 1fr)", minWidth: 96, priority: 4,
      stacked: { line: 2, slot: "fill" },
      sortValue: (c) => c.agent_name.toLowerCase(),
      cell: (c) => {
        const d = deskCell(c.agent_name);
        return <span title={d.text} className={cn("block truncate text-sm", d.muted ? muted : "text-text-primary")}>{d.text}</span>;
      },
    },
    {
      id: "channel", header: "Channel", width: "156px", minWidth: 156, priority: 5,
      sortValue: (c) => CHANNEL_KIND_LABEL[channelKind(c)],
      cell: (c) => {
        const kind = channelKind(c);
        const Icon = CHANNEL_ICON[kind];
        return (
          <span className="flex items-center gap-2 whitespace-nowrap text-sm text-text-primary">
            <Icon aria-hidden className="size-4 shrink-0 text-text-secondary" />{CHANNEL_KIND_LABEL[kind]}
          </span>
        );
      },
    },
    {
      id: "outcome", header: "Outcome", width: "124px", minWidth: 124, required: true,
      stacked: { line: 1, slot: "end" },
      sortValue: (c) => OUTCOME_ORDER.indexOf(c.outcome.key),
      cell: (c) => <OutcomePill outcome={c.outcome} />,
    },
    {
      id: "booking", header: "Booking", width: "minmax(168px, 1.4fr)", minWidth: 168, priority: 1,
      stacked: { line: 2, slot: "end" },
      sortValue: (c) => (c.booking ? `${c.booking.date ?? ""} ${c.booking.time ?? ""}` : null),
      cell: (c, { stacked }) => {
        const text = bookingText(c.booking);
        if (!c.booking || !text) return stacked ? null : <span className={cn("text-sm", muted)} aria-label="No booking">—</span>;
        return (
          <Link
            href={`/desk/bookings?booking=${encodeURIComponent(c.booking.id)}`}
            className="block truncate whitespace-nowrap text-sm font-medium text-accent-primary underline-offset-2 hover:underline"
            aria-label={`Open booking ${text}`}
          >
            {text}
          </Link>
        );
      },
    },
    {
      id: "duration", header: "Duration", width: "72px", minWidth: 72, align: "end", priority: 3,
      sortValue: (c) => c.duration_s,
      cell: (c) => <span className={cn("text-sm tabular-nums", muted)}>{durationShort(c.duration_s) ?? "—"}</span>,
    },
    {
      id: "language", header: "Language", width: "88px", minWidth: 88, priority: 6,
      sortValue: (c) => languageLabel(c.language),
      cell: (c) => <span className={cn("block truncate text-sm", muted)}>{languageLabel(c.language) ?? "—"}</span>,
    },
    {
      id: "credits", header: "Credits", width: "80px", minWidth: 80, align: "end", priority: 7,
      sortValue: (c) => {
        const u = ctx.usage[c.id];
        return u && u.requests > 0 ? u.credits : null;
      },
      cell: (c) => {
        const u = ctx.usage[c.id];
        if (!u || u.requests === 0) return <span className={cn("text-sm", muted)} aria-label="No usage recorded">—</span>;
        return (
          <span className="inline-flex items-center justify-end gap-1.5 text-sm tabular-nums text-text-primary" title="Speech credits, estimated from rates; not an invoice">
            {u.degraded > 0 && <span role="img" aria-label="Gnani was busy or down during this call" className="size-1.5 rounded-full bg-accent-warning" />}
            {formatCredits(u.credits)}
          </span>
        );
      },
    },
  ];
}

function Transcript({ data }: { data: CallDetail }) {
  if (data.transcript.length === 0) return <p className="text-sm text-text-secondary">Nothing was said in this conversation, so there is no transcript.</p>;
  return (
    <ol className="space-y-2" aria-label="Transcript">
      {data.transcript.map((line, i) => {
        const agent = line.role === "agent";
        return (
          <li key={i} className={cn("flex", agent ? "justify-start" : "justify-end")}>
            <div className={cn("max-w-[88%] rounded-2xl px-3.5 py-2 text-sm", agent ? "bg-bg-elevated text-text-primary" : "bg-accent-primary/10 text-text-primary")}>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">{agent ? data.agent_name : "Caller"}</p>
              <p className="mt-0.5 whitespace-pre-wrap break-words">{line.text}</p>
              {!agent && data.agent_id && line.text.trim() && <Misheard agentId={data.agent_id} conversationId={data.id} turnIndex={i} text={line.text} />}
              {line.actions.map((a, j) => (
                <p key={j} className={cn("mt-1.5 flex items-start gap-1.5 text-xs", a.ok ? "text-text-secondary" : "text-accent-error")}>
                  <Wrench aria-hidden className="mt-0.5 size-3 shrink-0" />
                  <span>{a.ok ? a.summary : "The agent tried an action and it did not work."}</span>
                </p>
              ))}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-semibold text-text-secondary">{label}</dt>
      <dd className="mt-0.5 truncate text-sm text-text-primary">{children}</dd>
    </div>
  );
}

export interface CallDetailsProps {
  /** The conversation to show. `summary` is the row it came from; without it the pane reads the summary from the server. */
  id: string;
  summary?: CallSummary | null;
  ctx: CallViewContext;
  onClose: () => void;
  /** The call is live and the owner wants to listen in. */
  onListen?: (id: string) => void;
  /** A forgotten caller: the list's profiles should be read again. */
  onForgotten?: () => void;
  /** Changes each time the owner opens a call (a click or Enter); focus then moves to the pane's heading. */
  focusSignal?: number;
}

/** The details pane of the Calls list: what the old expanded card showed, plus the call's facts. */
export function CallDetails({ id, summary, ctx, onClose, onListen, onForgotten, focusSignal = 0 }: CallDetailsProps) {
  const { data, error, isLoading } = useSWR<CallDetail>(`/api/calls/${encodeURIComponent(id)}`, (p: string) => api.get<CallDetail>(p), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
  const heading = useRef<HTMLHeadingElement>(null);
  // Opening a call moves focus here; moving the highlight with the arrow keys must not.
  useEffect(() => { if (focusSignal) heading.current?.focus({ preventScroll: true }); }, [focusSignal]);
  const call: CallSummary | null = summary ?? data ?? null;
  const known = ctx.known[id];
  const live = ctx.live.has(id);

  if (!call) {
    return (
      <div className="p-5">
        <PaneBar onClose={onClose} />
        {error ? <p role="alert" className="mt-4 text-sm text-accent-error">Could not load this conversation. Try again in a moment.</p> : <Skeleton className="mt-4 h-24 w-full" />}
      </div>
    );
  }
  const who = callerCell(call, known?.profile.display_name, known?.profile.number_masked);
  const kind = channelKind(call);
  const day = dayKey(call.when, ctx.tz);
  const desk = deskCell(call.agent_name);
  const booking = bookingText(call.booking);
  const usage = ctx.usage[id];
  return (
    <div className="flex min-h-0 flex-col">
      <div className="space-y-4 px-5 pb-4 pt-4">
        <PaneBar onClose={onClose} />
        <div>
          <h2 ref={heading} tabIndex={-1} className={cn("font-display text-[1.5rem] font-normal leading-tight outline-none", who.muted ? muted : "text-text-primary")}>{who.text}</h2>
          <p className="mt-1 text-sm text-text-secondary">
            {day ? `${dayHeading(day, ctx.today)}, ` : ""}{clockLabel(call.when, ctx.tz)} · {CHANNEL_KIND_LABEL[kind]}
          </p>
        </div>

        {live && (
          <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-success-tint px-3.5 py-2.5">
            <p className="flex items-center gap-2 text-sm font-medium text-accent-success">
              <span aria-hidden className="size-2 rounded-full bg-accent-success motion-safe:animate-pulse" />This call is live
            </p>
            {onListen && <Button size="sm" variant="outline" onClick={() => onListen(id)}><Headphones className="size-4" />Listen in</Button>}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-1.5">
          <OutcomePill outcome={call.outcome} />
          {call.called_back && <Badge tone="secondary">Called back</Badge>}
          {call.practice && <Badge>Practice call</Badge>}
          {known?.returning && <Badge tone="primary">Returning caller</Badge>}
        </div>
        {(booking || call.outcome.detail) && (
          <p className="text-sm text-text-primary">
            {booking ? (
              <>
                Booking{" "}
                <Link href={`/desk/bookings?booking=${encodeURIComponent(call.booking!.id)}`} className="font-medium text-accent-primary underline-offset-2 hover:underline">{booking}</Link>
                {call.booking?.with ? ` with ${call.booking.with}` : ""}
              </>
            ) : call.outcome.detail}
          </p>
        )}

        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-y border-border-2 py-3.5">
          <Fact label="Front desk"><span className={desk.muted ? muted : undefined}>{desk.text}</span></Fact>
          <Fact label="Channel">{CHANNEL_KIND_LABEL[kind]}</Fact>
          <Fact label="Duration">{durationLabel(call.duration_s) ?? "—"}</Fact>
          <Fact label="Language">{languageLabel(call.language) ?? "—"}</Fact>
          <Fact label="Credits">{usage && usage.requests > 0 ? `${formatCredits(usage.credits)} (estimated)` : "—"}</Fact>
        </dl>
        <CallUsageLine callId={id} />
        {known && <CallerCard profile={known.profile} onForgotten={onForgotten ?? (() => {})} />}
      </div>
      <div className="border-t border-border-2 bg-bg-base/50 px-5 py-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-text-primary">Transcript</h3>
          <Link href={`/desk/calls/${encodeURIComponent(id)}`} className="text-sm font-medium text-accent-primary underline-offset-2 hover:underline">Open call view</Link>
        </div>
        {isLoading ? <Skeleton className="h-24 w-full" /> : error || !data ? <p role="alert" className="text-sm text-accent-error">Could not load this transcript. Try again in a moment.</p> : <Transcript data={data} />}
      </div>
    </div>
  );
}

function PaneBar({ onClose }: { onClose: () => void }) {
  return (
    <div className="flex items-center justify-between">
      <button type="button" onClick={onClose} className="-ml-2 inline-flex h-9 items-center gap-1.5 rounded-lg px-2 text-sm font-medium text-text-secondary hover:bg-bg-base hover:text-text-primary lg:hidden">
        <ArrowLeft aria-hidden className="size-4" />All calls
      </button>
      <button type="button" onClick={onClose} aria-label="Close details" className="-mr-2 ml-auto hidden size-9 place-items-center rounded-lg text-text-secondary hover:bg-bg-base hover:text-text-primary lg:grid">
        <X aria-hidden className="size-4" />
      </button>
    </div>
  );
}
