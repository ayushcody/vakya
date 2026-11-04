"use client";

import { useEffect, useRef } from "react";
import { useReducedMotion } from "motion/react";
import { CalendarCheck, CalendarSearch, CalendarX, Check, CircleAlert, Loader2, Search, Volume2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { ListeningChip } from "@/components/hearing/ListeningChip";
import { Misheard } from "@/components/hearing/Misheard";
import type { HearingState } from "@/components/hearing/liveHearing";
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
      return <CalendarCheck aria-hidden className={cls} />;
    case "free":
      return <Check aria-hidden className={cls} />;
    case "taken":
    case "refused":
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

/** One tool call as a chip: "Checking Thu 5 Nov 6 pm" while it runs, then "Checked Thu 5 Nov 6 pm: taken, offered 5:30 pm or 6:30 pm". */
export function ToolChip({ start, end }: { start: ToolEvent | null; end: ToolEvent | null }) {
  const e = end ?? start;
  if (!e) return null;
  const pending = !end;
  return (
    <div
      role="status"
      data-status={e.status}
      className={cn(
        "inline-flex max-w-full items-center gap-2.5 rounded-full border px-4 py-2 text-[15px] font-medium leading-snug",
        pending ? "border-border bg-bg-base text-text-secondary" : (TOOL_TONE[e.status] ?? "border-border bg-bg-base text-text-primary")
      )}
    >
      <ToolIcon e={e} pending={pending} />
      <span className="min-w-0">{e.label}</span>
    </div>
  );
}

function AgentBlock({ lines, now }: { lines: Extract<Line, { kind: "agent" }>[]; now: number }) {
  const first = lines[0].event;
  const readback = lines.some((l) => l.event.readback);
  const speaking = lines.some((l) => now >= l.playFrom && now < l.playUntil);
  return (
    <div className="max-w-[46rem]">
      <p className="mb-1.5 flex flex-wrap items-center gap-x-2 text-xs font-semibold uppercase tracking-[1px] text-text-secondary">
        Front desk
        <span className="font-normal normal-case tracking-normal">{languageNote(first.language, first.script ?? "latin", false)}</span>
        {readback && <span className="rounded-full bg-violet-200 px-2 py-0.5 text-[11px] normal-case tracking-normal text-violet-700 [[data-theme=dark]_&]:bg-accent-primary/15 [[data-theme=dark]_&]:text-accent-secondary">Read back</span>}
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
export function LiveTranscript({ lines, now, className, maxHeight, agentId, hearing }: {
  lines: Line[];
  now: number;
  className?: string;
  maxHeight?: string;
  /** With the front desk's id each caller line gets a "Misheard?" action that teaches that desk (components/hearing). */
  agentId?: string;
  /** The `hearing` events by turn: what the desk was listening for, shown with the caller's line of that turn. */
  hearing?: HearingState;
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

  // A turn the desk is listening to whose line has not arrived yet: its "Listening for" chip stands at the end.
  const spoken = new Set(lines.flatMap((l) => (l.kind === "caller" && l.event.turn_id ? [l.event.turn_id] : [])));
  const waiting = hearing ? hearing.order.filter((t) => !spoken.has(t)).slice(-1).flatMap((t) => hearing.byTurn[t] ?? []) : [];

  return (
    <div ref={box} role="log" aria-live="polite" aria-label="Call transcript" style={{ maxHeight }} className={cn("space-y-5", maxHeight && "overflow-y-auto pr-1 [scrollbar-width:thin]", className)}>
      {blocks.map((b) => {
        if (b.kind === "agent") return <AgentBlock key={b.lines[0].id} lines={b.lines} now={now} />;
        if (b.kind === "tool") return <div key={b.id}><ToolChip start={b.start} end={b.end} /></div>;
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

