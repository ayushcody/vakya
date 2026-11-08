"use client";

import { useEffect, useRef } from "react";
import { useReducedMotion } from "motion/react";
import { CalendarCheck, CalendarSearch, CalendarX, Check, CircleAlert, Loader2, Search, Volume2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { ListeningChip } from "@/components/hearing/ListeningChip";
import { Misheard } from "@/components/hearing/Misheard";
import type { HearingState } from "@/components/hearing/liveHearing";
import { DeliveryChips, type Delivery } from "./Delivery";
import { clockWithSeconds, ms } from "./time";
import { languageNote, type Line, type ToolEvent, type WordLang } from "@/lib/liveCall";

/** A soft underline per language, in the page's own colours; a word whose language we cannot tell gets a dotted grey one. */
const UNDERLINE: Record<WordLang, string> = {
  hi: "decoration-[color-mix(in_srgb,var(--color-orange-600)_60%,transparent)]",
  mr: "decoration-[color-mix(in_srgb,var(--color-violet-600)_55%,transparent)]",
  en: "decoration-[color-mix(in_srgb,var(--color-blue-700)_50%,transparent)]",
  x: "decoration-dotted decoration-text-secondary/60",
};
const SWATCH: Record<WordLang, string> = {
  hi: "border-[color-mix(in_srgb,var(--color-orange-600)_60%,transparent)]",
  mr: "border-[color-mix(in_srgb,var(--color-violet-600)_55%,transparent)]",
  en: "border-[color-mix(in_srgb,var(--color-blue-700)_50%,transparent)]",
  x: "border-dotted border-text-secondary/60",
};

export interface Run {
  lang: WordLang;
  text: string;
}

/** Words as runs of one language: "Thursday ko 6 baje free hai?" is one Hindi run with English inside only where the English is. Numbers
 *  and punctuation (nothing to tell a language by) stay with the words around them. */
export function groupRuns(words: [string, WordLang][]): Run[] {
  const runs: Run[] = [];
  let lead = "";                 // letterless words at the start, kept for the first run
  for (const [w, lang] of words) {
    if (!/\p{L}/u.test(w)) {
      if (runs.length) runs[runs.length - 1].text += ` ${w}`;
      else lead = lead ? `${lead} ${w}` : w;
      continue;
    }
    const last = runs[runs.length - 1];
    if (last && last.lang === lang) last.text += ` ${w}`;
    else runs.push({ lang, text: lead ? `${lead} ${w}` : w });
    lead = "";
  }
  if (!runs.length && lead) runs.push({ lang: "x", text: lead });
  return runs;
}

/** A line of text, calm: plain words with a soft coloured underline under each run of one language (Hindi, Marathi, English), and a
 *  dotted grey one where we cannot tell. */
export function Words({ words, text }: { words?: [string, WordLang][]; text: string }) {
  const list = words && words.length ? words : text.split(/\s+/).filter(Boolean).map((w) => [w, "x"] as [string, WordLang]);
  const runs = groupRuns(list);
  return (
    <>
      {runs.map((r, i) => (
        <span key={i}>
          <span
            data-lang={r.lang}
            className={cn("underline decoration-[1.5px] underline-offset-[7px] [box-decoration-break:clone] [text-decoration-skip-ink:none]", UNDERLINE[r.lang] ?? UNDERLINE.x)}
          >
            {r.text}
          </span>
          {i < runs.length - 1 ? " " : ""}
        </span>
      ))}
    </>
  );
}

export function Legend({ className }: { className?: string }) {
  const items: [WordLang, string][] = [["hi", "Hindi"], ["mr", "Marathi"], ["en", "English"], ["x", "Not sure"]];
  return (
    <ul aria-label="What the underlines mean" className={cn("flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-secondary", className)}>
      {items.map(([lang, name]) => (
        <li key={lang} className="flex items-center gap-1.5">
          <span aria-hidden className={cn("inline-block h-0 w-5 border-b-[1.5px]", SWATCH[lang])} />
          {name}
        </li>
      ))}
    </ul>
  );
}

const TOOL_TONE: Record<string, string> = {
  free: "border-[color-mix(in_srgb,var(--color-accent-success)_45%,transparent)] bg-success-tint text-accent-success",
  booked: "border-[color-mix(in_srgb,var(--color-accent-success)_45%,transparent)] bg-success-tint text-accent-success",
  moved: "border-[color-mix(in_srgb,var(--color-accent-success)_45%,transparent)] bg-success-tint text-accent-success",
  cancelled: "border-[color-mix(in_srgb,var(--color-orange-700)_50%,transparent)] bg-orange-200 text-[#8a3b08] [[data-theme=dark]_&]:bg-accent-warning/15 [[data-theme=dark]_&]:text-warning-text",
  taken: "border-[color-mix(in_srgb,var(--color-orange-700)_50%,transparent)] bg-orange-200 text-[#8a3b08] [[data-theme=dark]_&]:bg-accent-warning/15 [[data-theme=dark]_&]:text-warning-text",
  refused: "border-[color-mix(in_srgb,var(--color-orange-700)_50%,transparent)] bg-orange-200 text-[#8a3b08] [[data-theme=dark]_&]:bg-accent-warning/15 [[data-theme=dark]_&]:text-warning-text",
  readback: "border-[color-mix(in_srgb,var(--color-violet-700)_45%,transparent)] bg-violet-200 text-violet-700 [[data-theme=dark]_&]:bg-accent-primary/15 [[data-theme=dark]_&]:text-accent-secondary",
  confirm: "border-[color-mix(in_srgb,var(--color-violet-700)_45%,transparent)] bg-violet-200 text-violet-700 [[data-theme=dark]_&]:bg-accent-primary/15 [[data-theme=dark]_&]:text-accent-secondary",
  failed: "border-[color-mix(in_srgb,var(--color-accent-error)_45%,transparent)] bg-error-tint text-accent-error",
};

function ToolIcon({ e, pending }: { e: ToolEvent; pending: boolean }) {
  const cls = "size-[18px] shrink-0";
  if (pending) return <Loader2 aria-hidden className={cn(cls, "animate-spin motion-reduce:animate-none")} />;
  switch (e.status) {
    case "booked":
    case "moved":
      return <CalendarCheck aria-hidden className={cls} />;
    case "free":
      return <Check aria-hidden className={cls} />;
    case "taken":
    case "refused":
    case "cancelled":
      return <CalendarX aria-hidden className={cls} />;
    case "readback":
    case "confirm":
      return <Volume2 aria-hidden className={cls} />;
    case "failed":
      return <CircleAlert aria-hidden className={cls} />;
    case "listed":
    case "found":
      return <Search aria-hidden className={cls} />;
    default:
      return <CalendarSearch aria-hidden className={cls} />;
  }
}

const TITLE: Record<string, string> = {
  check_availability: "Checked availability", list_free_slots: "Looked at free times", book: "Booking", cancel: "Cancellation",
  reschedule: "Move", move: "Move", find_booking: "Looked up bookings",
};
const DONE = ["booked", "moved", "cancelled"];

/** One thing the agent did, as a card, with the time it happened: "Checked availability · Thu 5 Nov, 6 pm: taken, offered ..." while it runs a
 *  spinner, then the result. A booking, move or cancellation also shows its WhatsApp and calendar chips. */
export function ToolChip({ start, end, delivery }: { start: ToolEvent | null; end: ToolEvent | null; delivery?: Delivery }) {
  const e = end ?? start;
  if (!e) return null;
  const pending = !end;
  const time = clockWithSeconds(e.at);
  const title = e.status === "readback" ? "Read back" : (TITLE[e.tool] ?? "Action");
  return (
    <div
      role="status"
      data-status={e.status}
      className={cn(
        "max-w-[46rem] rounded-2xl border px-4 py-2.5 leading-snug",
        pending ? "border-border bg-bg-base text-text-secondary" : (TOOL_TONE[e.status] ?? "border-border bg-bg-base text-text-primary")
      )}
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5"><ToolIcon e={e} pending={pending} /></span>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-x-2 text-[11px] font-semibold uppercase tracking-[1px] opacity-80">
            {title}
            {time && <span className="font-normal normal-case tracking-normal">{time}</span>}
          </p>
          <p className="text-[15px] font-medium">{e.label}</p>
        </div>
      </div>
      {delivery && end && DONE.includes(end.status) && end.booking_id && (
        <DeliveryChips bookingId={end.booking_id} status={end.status} at={end.at} delivery={delivery} />
      )}
    </div>
  );
}

function AgentBlock({ lines, now, replyMs }: { lines: Extract<Line, { kind: "agent" }>[]; now: number; replyMs?: number | null }) {
  const first = lines[0].event;
  const readback = lines.some((l) => l.event.readback);
  const speaking = lines.some((l) => now >= l.playFrom && now < l.playUntil);
  return (
    <div className="max-w-[46rem]">
      <p className="mb-1.5 flex flex-wrap items-center gap-x-2 text-xs font-semibold uppercase tracking-[1px] text-text-secondary">
        Front desk
        <span className="font-normal normal-case tracking-normal">{languageNote(first.language, first.script ?? "latin", false)}</span>
        {readback && <span className="rounded-full bg-violet-200 px-2 py-0.5 text-[11px] normal-case tracking-normal text-violet-700 [[data-theme=dark]_&]:bg-accent-primary/15 [[data-theme=dark]_&]:text-accent-secondary">Read back</span>}
        {replyMs != null && (
          <span
            data-testid="reply-latency"
            title="From the caller's words arriving to the first sentence of the reply being ready. The sound itself starts a little after."
            className="text-[11px] font-normal normal-case tracking-normal tabular-nums"
          >
            first reply {ms(replyMs)}
          </span>
        )}
        {speaking && (
          <span className="inline-flex items-center gap-1 text-[11px] normal-case tracking-normal text-text-secondary">
            <span aria-hidden className="size-1.5 rounded-full bg-accent-primary motion-safe:animate-pulse" />
            speaking
          </span>
        )}
      </p>
      <p
        className={cn(
          "rounded-2xl text-[1.625rem] leading-[1.45] text-text-primary transition-colors duration-500",
          readback && "bg-[color-mix(in_srgb,var(--color-violet-600)_8%,transparent)] px-4 py-3 -mx-4"
        )}
      >
        {lines.map((l, i) => {
          const active = readback && l.event.readback && now >= l.playFrom && now < l.playUntil;
          return (
            <span key={l.id}>
              <span data-active={active ? "true" : undefined} className={cn(active && "rounded-md bg-[color-mix(in_srgb,var(--color-violet-600)_16%,transparent)] [box-decoration-break:clone]")}>
                <Words words={l.event.words} text={l.event.text} />
              </span>
              {i < lines.length - 1 ? " " : ""}
            </span>
          );
        })}
      </p>
    </div>
  );
}

/** The transcript of the call: the caller's and the front desk's words in large type, tool-call chips between them. */
export function LiveTranscript({ lines, now, className, maxHeight, agentId, hearing, delivery }: {
  lines: Line[];
  now: number;
  className?: string;
  maxHeight?: string;
  /** With the front desk's id each caller line gets a "Misheard?" action that teaches that desk (components/hearing). */
  agentId?: string;
  /** The `hearing` events by turn: what the desk was listening for, shown with the caller's line of that turn. */
  hearing?: HearingState;
  /** WhatsApp and calendar state of the bookings the agent made (see Delivery.tsx). */
  delivery?: Delivery;
}) {
  const box = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  // Follow the call by scrolling this box only: scrolling the page under the person watching would move the calendar away.
  useEffect(() => {
    const el = box.current;
    if (el && el.scrollTo) el.scrollTo({ top: el.scrollHeight, behavior: reduce ? "auto" : "smooth" });
  }, [lines.length, hearing?.lastSeq, reduce]);

  // Consecutive sentences of the front desk read as one paragraph.
  const blocks: ({ kind: "agent"; lines: Extract<Line, { kind: "agent" }>[] } | Exclude<Line, { kind: "agent" }>)[] = [];
  for (const l of lines) {
    const last = blocks[blocks.length - 1];
    if (l.kind === "agent" && last && last.kind === "agent") last.lines.push(l);
    else if (l.kind === "agent") blocks.push({ kind: "agent", lines: [l] });
    else blocks.push(l);
  }

  // How long the reply took: the caller's line to the first sentence after it, when the two are far enough apart to be a measurement
  // (a replayed call stores both at one instant, and then nothing is shown).
  const replyMs = (b: { lines: Extract<Line, { kind: "agent" }>[] }) => {
    const first = b.lines[0];
    const i = lines.indexOf(first);
    for (let k = i - 1; k >= 0; k--) {
      const l = lines[k];
      if (l.kind === "agent") return null;
      if (l.kind === "caller") {
        const d = Date.parse(first.event.at) - Date.parse(l.event.at);
        return Number.isFinite(d) && d >= 100 && d < 60000 ? d : null;
      }
    }
    return null;
  };

  // A turn the desk is listening to whose line has not arrived yet: its "Listening for" chip stands at the end.
  const spoken = new Set(lines.flatMap((l) => (l.kind === "caller" && l.event.turn_id ? [l.event.turn_id] : [])));
  const waiting = hearing ? hearing.order.filter((t) => !spoken.has(t)).slice(-1).flatMap((t) => hearing.byTurn[t] ?? []) : [];

  return (
    <div ref={box} role="log" aria-live="polite" aria-label="Call transcript" style={{ maxHeight }} className={cn("space-y-5", maxHeight && "overflow-y-auto pr-1 [scrollbar-width:thin]", className)}>
      {blocks.map((b) => {
        if (b.kind === "agent") return <AgentBlock key={b.lines[0].id} lines={b.lines} now={now} replyMs={replyMs(b)} />;
        if (b.kind === "tool") return <div key={b.id}><ToolChip start={b.start} end={b.end} delivery={delivery} /></div>;
        const e = b.event;
        return (
          <div key={b.id} className="ml-auto max-w-[46rem] text-left sm:pl-12">
            <p className="mb-1.5 flex flex-wrap items-center gap-x-2 text-xs font-semibold uppercase tracking-[1px] text-text-secondary">
              Caller
              <span className="font-normal normal-case tracking-normal">{languageNote(e.language, e.script, e.unsure)}</span>
              {e.typed && <span className="font-normal normal-case tracking-normal">typed</span>}
            </p>
            <p lang={e.language === "hi" || e.language === "mr" ? e.language : undefined} className="text-[1.625rem] leading-[1.45] text-text-primary">
              <Words words={e.words} text={e.text} />
            </p>
            {(hearing?.byTurn[e.turn_id ?? ""] ?? []).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(hearing?.byTurn[e.turn_id ?? ""] ?? []).map((h) => <ListeningChip key={h.seq} event={h} />)}
              </div>
            )}
            {agentId && e.conversation_id && !e.typed && (
              <Misheard agentId={agentId} conversationId={e.conversation_id} text={e.text} />
            )}
          </div>
        );
      })}
      {waiting.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {waiting.map((h) => <ListeningChip key={h.seq} event={h} />)}
        </div>
      )}
    </div>
  );
}

