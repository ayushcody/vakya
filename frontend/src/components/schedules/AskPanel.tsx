"use client";

import { useState, type FormEvent } from "react";
import { ArrowUp, Check, Clock3, Phone, X } from "lucide-react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { CHIP_FOCUS, CHIP_ON } from "./form/chipStyle";
import { checkSchedule, plainError, slotWhen, type CheckOption, type CheckReason, type CheckResult, type Schedule } from "@/lib/schedules";

const EXAMPLES_SLOTS = ["Is Thursday 6 pm free?", "kal shaam 6 baje", "Saturday morning"];
const EXAMPLES_NIGHTS = ["Is Friday free for 2 nights?", "kal se do raat", "Next Saturday night"];

const REASONS: Record<CheckReason, string> = {
  closed: "Closed that day",
  full: "Already full",
  blocked: "Marked busy",
  past: "That time has passed",
  outside_hours: "Outside opening hours",
  too_soon: "Too soon to book",
  too_far: "Too far ahead",
  not_a_start: "Not when a slot starts",
  unclear: "Could not tell which time",
};

/**
 * "Ask like a caller": type what a caller would say and see the answer the receptionist would give, from the same
 * availability code the phone agent uses, with the two nearest free times it would offer.
 */
export function AskPanel({ schedule, onHighlight, className }: { schedule: Schedule; onHighlight: (slots: CheckOption[]) => void; className?: string }) {
  const [text, setText] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [lang, setLang] = useState<"en" | "hi">("en");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<"before" | "after" | null>(null);
  const examples = schedule.kind === "nights" ? EXAMPLES_NIGHTS : EXAMPLES_SLOTS;

  async function ask(q: string) {
    const query = q.trim();
    if (!query) return;
    setBusy(true);
    setError(null);
    setPicked(null);
    onHighlight([]);
    try {
      const r = await checkSchedule(schedule.id, { text: query });
      setAsked(query);
      setResult(r);
      if (r.available && r.requested) onHighlight([r.requested]);
    } catch (e) {
      setError(plainError(e, "Could not check that. Try again in a moment."));
    } finally {
      setBusy(false);
    }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void ask(text);
  };
  const choose = (which: "before" | "after", o: CheckOption) => {
    setPicked(which);
    onHighlight([o]);
  };

  return (
    <section aria-labelledby="ask-title" className={cn("rounded-2xl border border-border bg-bg-surface p-5", className)}>
      <h2 id="ask-title" className="flex items-center gap-2 font-display text-xl text-text-primary">
        <Phone aria-hidden className="size-4 text-text-secondary" /> Ask like a caller
      </h2>
      <p className="mt-1 text-sm text-text-secondary">Type what a caller would say. You see the answer the front desk gives, in English or Hindi.</p>

      <form onSubmit={submit} className="mt-4 flex gap-2">
        <label className="sr-only" htmlFor="ask-input">What the caller says</label>
        <input
          id="ask-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={examples[0]}
          autoComplete="off"
          className="h-10 min-w-0 flex-1 rounded-lg border border-border bg-bg-surface px-3 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
        />
        <Button type="submit" aria-label="Ask" loading={busy} disabled={!text.trim()} className="w-10 shrink-0 px-0">
          <ArrowUp aria-hidden className="size-4" />
        </Button>
      </form>
      <div className="mt-3 flex flex-wrap gap-2">
        {examples.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => {
              setText(ex);
              void ask(ex);
            }}
            className="min-h-9 rounded-full border border-border bg-bg-base px-3 text-[13px] text-text-primary transition-colors hover:bg-bg-elevated focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
          >
            {ex}
          </button>
        ))}
      </div>

      {error && <p role="alert" className="mt-4 text-sm text-accent-error">{error}</p>}

      {result && asked && (
        <div className="mt-5 space-y-3" aria-live="polite">
          <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-bg-base px-3.5 py-2 text-sm text-text-primary">{asked}</p>
          <div className="max-w-[92%] rounded-2xl rounded-bl-md border border-border bg-bg-elevated px-3.5 py-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold uppercase tracking-[0.5px]",
                  result.available ? "bg-success-tint text-accent-success" : "bg-orange-200 text-[#a1470d]"
                )}
              >
                {result.available ? <Check aria-hidden className="size-3" /> : <X aria-hidden className="size-3" />}
                {result.available ? "Free" : (result.reason && REASONS[result.reason]) || "Not free"}
              </span>
              <div role="group" aria-label="Language of the answer" className="inline-flex shrink-0 overflow-hidden rounded-md border border-border text-xs font-semibold">
                {(["en", "hi"] as const).map((l) => (
                  <button
                    key={l}
                    type="button"
                    aria-pressed={lang === l}
                    onClick={() => setLang(l)}
                    className={cn("min-h-7 px-2.5", CHIP_FOCUS, "focus-visible:-outline-offset-2", lang === l ? CHIP_ON : "bg-bg-surface text-text-secondary hover:bg-bg-base")}
                  >
                    {l === "en" ? "English" : "हिन्दी"}
                  </button>
                ))}
              </div>
            </div>
            <p lang={lang} className="text-[15px] leading-relaxed text-text-primary">{result.spoken[lang] || result.spoken.en}</p>
          </div>

          {(result.before || result.after) && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-text-secondary">The two nearest free times it offers. Pick one to see it in the calendar.</p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                {(["before", "after"] as const).map((k) => {
                  const o = result[k];
                  if (!o) return null;
                  return (
                    <button
                      key={k}
                      type="button"
                      aria-pressed={picked === k}
                      onClick={() => choose(k, o)}
                      className={cn(
                        "flex min-h-14 flex-col items-start justify-center rounded-xl border px-3 py-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary",
                        picked === k ? "border-accent-primary bg-violet-200/60" : "border-border bg-bg-surface hover:bg-bg-base"
                      )}
                    >
                      <span className="flex items-center gap-1 text-xs text-text-secondary"><Clock3 aria-hidden className="size-3" />{k === "before" ? "Earlier" : "Later"}</span>
                      <span className="text-sm font-semibold text-text-primary">{slotWhen(o.start)}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
