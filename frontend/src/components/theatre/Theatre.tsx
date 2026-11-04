"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ChevronRight, XCircle } from "lucide-react";
import { API_URL } from "@/lib/apiBase";
import { Badge, Skeleton } from "@/components/ui";
import { DeskIllustration } from "@/components/illustrations/DeskIllustration";
import { cn } from "@/lib/utils";
import { CallPlayer } from "./CallPlayer";
import { INPUT_NAME, MODE_NAME, lineLabel, newestPerKind, percent, seconds } from "./format";
import type { TheatreRun, TheatreRunRow, TheatreScenario } from "./types";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "empty" } | { state: "ready"; run: TheatreRun };

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(`${API_URL}${path}`, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`the server answered ${r.status}`);
  return (await r.json()) as T;
}

function Strip({ run }: { run: TheatreRun }) {
  const s = run.summary;
  const items = [
    { label: "Passed", value: `${s.passed} of ${s.scenarios}`, note: s.failed ? `${s.failed} failed, shown below` : "none failed" },
    { label: "Fields correct", value: percent(s.fields_correct_pct), note: `${s.fields_correct} of ${s.fields_total} booking fields` },
    run.mode === "scripted"
      ? { label: "Median first reply", value: "not measured", note: "scripted model, latency not meaningful: it answers instantly" }
      : {
          label: run.input === "text" ? "Median first reply (text)" : "Median first reply",
          value: seconds(s.median_first_reply_ms),
          note: run.input === "text" ? "reply text, without the spoken reply" : "to the first spoken answer",
        },
    { label: "Median turns to complete", value: s.median_turns_to_complete == null ? "n/a" : String(s.median_turns_to_complete), note: "caller turns until booked or cancelled" },
  ];
  return (
    <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Summary of this run">
      {items.map((it) => (
        <div key={it.label} className="rounded-2xl border border-border bg-bg-surface p-4">
          <dt className="text-xs font-semibold uppercase tracking-[1px] text-text-secondary">{it.label}</dt>
          <dd className="mt-1 font-display text-3xl tabular-nums text-text-primary">{it.value}</dd>
          <dd className="mt-0.5 text-xs text-text-secondary">{it.note}</dd>
        </div>
      ))}
    </dl>
  );
}

function Card({ s, run, onOpen }: { s: TheatreScenario; run: TheatreRun; onOpen: () => void }) {
  const pass = s.verdict.pass;
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className={cn(
          "group flex h-full w-full flex-col overflow-hidden rounded-2xl border bg-bg-surface text-left transition-colors hover:border-accent-primary",
          pass ? "border-border" : "border-accent-error/50"
        )}
        aria-label={`Open scenario ${s.n}, ${s.title}: ${pass ? "passed" : "failed"}`}
      >
        <DeskIllustration id={s.template} className="bg-bg-base" />
        <span className="flex flex-1 flex-col gap-2 p-4">
          <span className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold uppercase tracking-[0.5px] text-text-secondary">Scenario {s.n}</span>
            <Badge tone={pass ? "success" : "error"} className="gap-1">
              {pass ? <CheckCircle2 className="size-3.5" aria-hidden /> : <XCircle className="size-3.5" aria-hidden />}
              {pass ? "Pass" : "Fail"}
            </Badge>
          </span>
          <span className="font-display text-lg leading-snug text-text-primary">{s.title}</span>
          <span className="text-sm leading-relaxed text-text-secondary">{s.persona}</span>
          <span className="mt-auto flex flex-wrap gap-1.5 pt-1 text-xs text-text-secondary">
            <span className="rounded-md border border-border px-2 py-0.5">{s.language_label}</span>
            <span className="rounded-md border border-border px-2 py-0.5">{lineLabel(s, run.input)}</span>
          </span>
          <span className="text-xs leading-snug text-text-secondary">{s.verdict.reason}</span>
          <span className="inline-flex items-center gap-1 text-sm font-medium text-accent-primary">
            Open the call <ChevronRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
          </span>
        </span>
      </button>
    </li>
  );
}

/** Caller Theatre: nine synthetic callers, each run end to end and scored from the end state. Shared by /theatre and /desk/theatre. */
export function Theatre() {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [kinds, setKinds] = useState<TheatreRunRow[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [latest, runs] = await Promise.all([
          getJson<{ run: TheatreRun | null }>("/api/theatre/latest"),
          getJson<{ runs: TheatreRunRow[] }>("/api/theatre/runs").catch(() => ({ runs: [] })),
        ]);
        if (cancelled) return;
        setKinds(newestPerKind(runs.runs));
        setLoad(latest.run ? { state: "ready", run: latest.run } : { state: "empty" });
      } catch (e) {
        if (!cancelled) setLoad({ state: "error", message: e instanceof Error ? e.message : "unknown error" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = () => {
    setLoad({ state: "loading" });
    setAttempt((n) => n + 1);
  };

  const pick = async (r: TheatreRunRow) => {
    setOpen(null);
    setLoad({ state: "loading" });
    try {
      const got = await getJson<{ run: TheatreRun }>(`/api/theatre/runs/${r.run_id}?mode=${r.mode}&input=${r.input}`);
      setLoad({ state: "ready", run: got.run });
    } catch (e) {
      setLoad({ state: "error", message: e instanceof Error ? e.message : "unknown error" });
    }
  };

  if (load.state === "loading") {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading the latest run">
        <Skeleton className="h-8 w-2/3" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 rounded-2xl" />
          ))}
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-72 rounded-2xl" />
          ))}
        </div>
      </div>
    );
  }
  if (load.state === "error") {
    return (
      <div role="alert" className="rounded-2xl border border-border bg-bg-surface p-5 text-sm text-text-secondary">
        <p className="font-semibold text-text-primary">The results could not be loaded.</p>
        <p className="mt-1">The server did not answer ({load.message}). Start it with ./vakya start and reload.</p>
        <button type="button" onClick={retry} className="mt-3 inline-flex min-h-10 items-center rounded-full border border-border px-4 font-medium text-text-primary hover:border-accent-primary">
          Try again
        </button>
      </div>
    );
  }
  if (load.state === "empty") {
    return (
      <div className="rounded-2xl border border-border bg-bg-surface p-5 text-sm text-text-secondary">
        <p className="font-semibold text-text-primary">No run yet.</p>
        <p className="mt-1">
          Run <code className="rounded bg-bg-base px-1.5 py-0.5 font-mono text-xs">backend/.venv/bin/python backend/bench/theatre/runner.py --scripted-llm</code> and reload this page.
        </p>
      </div>
    );
  }

  const run = load.run;
  const current = run.scenarios.find((s) => s.id === open) ?? null;
  return (
    <div className="space-y-8 text-text-primary">
      <div className="space-y-3">
        <p className="font-display text-xl leading-snug sm:text-2xl">{run.label}</p>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone="primary">{MODE_NAME[run.mode]}</Badge>
          <Badge tone={run.input === "audio" ? "primary" : "warning"}>{run.input === "audio" ? "Audio run" : "Text run, audio run pending"}</Badge>
          <span className="text-text-secondary">
            Run {run.created_at.slice(0, 16).replace("T", " ")}. {run.mode_label}.
          </span>
        </div>
        {kinds.length > 1 && (
          <div role="group" aria-label="Choose a run" className="flex flex-wrap gap-2">
            {kinds.map((k) => {
              const active = k.run_id === run.run_id && k.mode === run.mode && k.input === run.input;
              return (
                <button
                  key={`${k.mode}-${k.input}`}
                  type="button"
                  aria-pressed={active}
                  onClick={() => void pick(k)}
                  className={cn(
                    "min-h-10 rounded-full border px-4 text-sm font-medium transition-colors",
                    active ? "border-accent-primary bg-accent-primary/10 text-text-primary" : "border-border text-text-secondary hover:border-accent-primary"
                  )}
                >
                  {MODE_NAME[k.mode]}, {INPUT_NAME[k.input].toLowerCase()}: {k.summary.passed} of {k.summary.scenarios} passed
                </button>
              );
            })}
          </div>
        )}
      </div>

      <aside role="note" className="grid gap-4 rounded-2xl border border-border bg-bg-surface p-5 text-sm leading-relaxed text-text-secondary md:grid-cols-2 md:gap-8">
        {run.input === "text" && (
          <div>
            <p className="font-semibold text-text-primary">A text run.</p>
            <p className="mt-1">
              The callers&apos; words went to the turn endpoint as typed text, so speech recognition, the caller voices and the line noise were not
              exercised; the noise on each card is planned. The audio run switches on by itself once the Gnani key is set. Scoring is identical.
            </p>
          </div>
        )}
        {run.mode === "scripted" && (
          <div>
            <p className="font-semibold text-text-primary">A scripted model.</p>
            <p className="mt-1">
              Tools, bookings, availability, read-back and confirm-before-cancel are the real rules. The wording and the choice of the next tool come
              from a deterministic stand-in, so a pass shows the path works, not what a language model does with the same calls.
            </p>
          </div>
        )}
      </aside>

      <Strip run={run} />

      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Scenarios">
        {run.scenarios.map((s) => (
          <Card key={s.id} s={s} run={run} onOpen={() => setOpen(s.id)} />
        ))}
      </ul>

      <section aria-labelledby="theatre-limits" className="text-sm text-text-secondary">
        <h2 id="theatre-limits" className="font-semibold text-text-primary">What this does not show</h2>
        <ul className="mt-1.5 list-disc space-y-1 pl-5">
          {run.limits.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </section>

      {current && <CallPlayer scenario={current} run={run} onClose={() => setOpen(null)} />}
    </div>
  );
}
