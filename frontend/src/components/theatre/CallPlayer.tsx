"use client";

import { useEffect, useRef } from "react";
import { CheckCircle2, CircleAlert, Info, X, XCircle } from "lucide-react";
import { Badge } from "@/components/ui";
import { DeskIllustration } from "@/components/illustrations/DeskIllustration";
import { cn } from "@/lib/utils";
import { PlayButton } from "./PlayButton";
import { audioUrl, lineLabel, percent, seconds, toolChip } from "./format";
import type { TheatreCheck, TheatreRun, TheatreScenario, TheatreTurn } from "./types";

const CHIP_TONE = {
  ok: "border-accent-success/40 bg-accent-success/10 text-text-primary",
  note: "border-accent-warning/50 bg-accent-warning/10 text-text-primary",
  bad: "border-accent-error/50 bg-accent-error/10 text-text-primary",
} as const;

function ToolChips({ turn }: { turn: TheatreTurn }) {
  const calls = turn.agent.tool_calls ?? [];
  if (!calls.length) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Tool calls in this turn">
      {calls.map((c, i) => {
        const chip = toolChip(c);
        const detail = c.arguments ? Object.entries(c.arguments).filter(([k]) => k !== "phone").map(([k, v]) => `${k} ${String(v)}`).join(", ") : "";
        return (
          <li key={i} className={cn("rounded-md border px-2 py-1 text-xs", CHIP_TONE[chip.tone])} title={c.result || undefined}>
            <span className="font-mono text-[11px] text-text-secondary">{chip.raw}</span>
            <span className="mx-1 text-text-secondary" aria-hidden>·</span>
            {chip.text}
            {detail && <span className="block text-[11px] text-text-secondary">{detail}</span>}
          </li>
        );
      })}
    </ul>
  );
}

function Bubble({ turn, input, scripted }: { turn: TheatreTurn; input: TheatreRun["input"]; scripted: boolean }) {
  const heard = turn.heard && turn.heard.trim() !== turn.caller.text.trim() ? turn.heard : null;
  const callerClip = audioUrl(turn.caller.audio);
  const cleanClip = audioUrl(turn.caller.audio_clean);
  const agentClip = audioUrl(turn.agent.audio);
  return (
    <li className="space-y-2">
      <div className="flex">
        <div className="max-w-[92%] rounded-2xl rounded-tl-sm border border-border bg-bg-base px-4 py-3 sm:max-w-[80%]">
          <p className="text-xs font-semibold uppercase tracking-[0.5px] text-text-secondary">
            Caller, turn {turn.i}
            {turn.caller.repeat && " (said again)"}
          </p>
          <p lang="und" className="mt-1 text-sm leading-relaxed text-text-primary">{turn.caller.text}</p>
          {heard && (
            <p className="mt-1.5 text-xs text-text-secondary">
              The agent heard: <span className="text-text-primary">{heard}</span>
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {callerClip ? (
              <>
                <PlayButton src={callerClip} label={`caller on the phone line${turn.caller.snr_db != null ? ` (${turn.caller.snr_db} dB)` : ""}`} />
                {cleanClip && <PlayButton src={cleanClip} label="before the line" />}
              </>
            ) : (
              input === "text" && <span className="text-xs text-text-secondary">Typed, no audio in a text run.</span>
            )}
          </div>
        </div>
      </div>
      <div className="flex justify-end">
        <div className="max-w-[92%] rounded-2xl rounded-tr-sm border border-accent-primary/30 bg-accent-primary/5 px-4 py-3 sm:max-w-[80%]">
          <p className="text-xs font-semibold uppercase tracking-[0.5px] text-text-secondary">Front desk</p>
          <p lang="und" className="mt-1 text-sm leading-relaxed text-text-primary">{turn.agent.text}</p>
          <ToolChips turn={turn} />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {agentClip && <PlayButton src={agentClip} label="the reply" />}
            <span className="text-xs text-text-secondary">
              {scripted ? "scripted model, latency not meaningful" : <>{input === "text" ? "reply text ready in" : "first reply in"} {seconds(turn.latency?.first_answer_ms)}</>}
            </span>
          </div>
        </div>
      </div>
    </li>
  );
}

function CheckRow({ c }: { c: TheatreCheck }) {
  const Icon = c.ok ? CheckCircle2 : c.must ? XCircle : CircleAlert;
  const tone = c.ok ? "text-accent-success" : c.must ? "text-accent-error" : "text-accent-warning";
  return (
    <li className="flex gap-2.5 py-2">
      <Icon className={cn("mt-0.5 size-4 shrink-0", tone)} aria-hidden />
      <div className="min-w-0 text-sm">
        <p className="font-medium text-text-primary">
          {c.label}
          <span className="sr-only">{c.ok ? ": passed" : c.must ? ": failed" : ": warning"}</span>
          {!c.must && <span className="ml-2 text-xs font-normal text-text-secondary">advisory</span>}
        </p>
        <p className="text-text-secondary">{c.detail}</p>
      </div>
    </li>
  );
}

/** One scenario opened: the call as bubbles, then what the scorer decided and why. */
export function CallPlayer({ scenario: s, run, onClose }: { scenario: TheatreScenario; run: TheatreRun; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement | null>(null);
  const m = s.verdict.metrics;

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (d.open) return;     // strict mode runs the effect twice
    if (typeof d.showModal === "function") d.showModal();
    else d.setAttribute("open", "");
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby="theatre-call-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className="m-auto w-[min(56rem,calc(100vw-1.5rem))] max-w-none rounded-2xl border border-border bg-bg-surface p-0 text-text-primary shadow-xl backdrop:bg-black/50"
    >
      <div className="max-h-[88vh] overflow-y-auto">
        <header className="z-10 flex items-start sm:sticky sm:top-0 gap-4 border-b border-border bg-bg-surface px-4 py-4 sm:px-6">
          <DeskIllustration id={s.template} size={96} className="hidden rounded-xl sm:block" />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-[0.5px] text-text-secondary">
              Scenario {s.n} · {s.language_label} · {lineLabel(s, run.input)}
            </p>
            <h2 id="theatre-call-title" className="font-display text-xl leading-snug sm:text-2xl">{s.title}</h2>
            <p className="mt-1 text-sm text-text-secondary">{s.persona}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone={s.verdict.pass ? "success" : "error"}>{s.verdict.pass ? "Pass" : "Fail"}</Badge>
              <span className="text-sm text-text-secondary">{s.verdict.reason}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close the call"
            className="inline-flex size-10 shrink-0 items-center justify-center rounded-full border border-border hover:border-accent-primary"
          >
            <X className="size-4" aria-hidden />
          </button>
        </header>

        <div className="space-y-6 px-4 py-5 sm:px-6">
          {s.setup.length > 0 && (
            <p className="flex gap-2 rounded-xl border border-border bg-bg-base p-3 text-sm text-text-secondary">
              <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>
                Set up before the call:{" "}
                {s.setup.map((x) => `a booking in the name of ${x.name} on ${x.date}${x.time ? ` at ${x.time}` : ""}`).join("; ")}.
              </span>
            </p>
          )}
          {s.error && (
            <p role="alert" className="rounded-xl border border-accent-error/40 bg-accent-error/10 p-3 text-sm">The scenario could not run: {s.error}</p>
          )}
          {s.turns.length > 0 ? (
            <ol className="space-y-5" aria-label="The call, turn by turn">
              {s.turns.map((t) => (
                <Bubble key={t.i} turn={t} input={run.input} scripted={run.mode === "scripted"} />
              ))}
            </ol>
          ) : (
            <p className="text-sm text-text-secondary">No turn was recorded.</p>
          )}
          {s.ended && <p className="text-center text-xs text-text-secondary">Call ended: {s.ended}.</p>}

          <section aria-labelledby="theatre-verdict" className="rounded-2xl border border-border bg-bg-base p-4 sm:p-5">
            <h3 id="theatre-verdict" className="font-display text-lg">What the scorer found</h3>
            <p className="mt-1 text-sm text-text-secondary">
              Scored from what ended up in the booking system, not from how the call sounded.
            </p>
            <ul className="mt-3 divide-y divide-border">
              {s.verdict.checks.map((c) => (
                <CheckRow key={c.id} c={c} />
              ))}
            </ul>
            <dl className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <Metric label="Fields correct" value={`${m.fields_correct} of ${m.fields_total}`} />
              <Metric label="Turns to complete" value={m.turns_to_complete == null ? "not completed" : `${m.turns_to_complete} of ${m.caller_turns}`} />
              <Metric label={run.input === "text" ? "First reply text" : "First reply"} value={run.mode === "scripted" ? "not measured (scripted model)" : seconds(m.first_reply_ms)} />
              <Metric label="Total time" value={run.mode === "scripted" ? "not measured (scripted model)" : seconds(m.total_ms)} />
              {m.readback_catch != null && <Metric label="Read-back caught the wrong digit" value={m.readback_catch ? "yes" : "no"} />}
              {m.read_back_before_commit != null && <Metric label="Read back before committing" value={m.read_back_before_commit ? "yes" : "no"} />}
              <Metric label="Fields correct (share)" value={percent(m.fields_correct_pct)} />
            </dl>
          </section>
        </div>
      </div>
    </dialog>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-text-secondary">{label}</dt>
      <dd className="font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
