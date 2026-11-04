"use client";

import { useState } from "react";
import { Sparkles, Undo2 } from "lucide-react";
import { Button } from "@/components/ui";
import { applyParsedHours, parseHours, plainError, type ScheduleDraft } from "@/lib/schedules";

export type Changed = "days" | "hours" | "slots" | "holidays";

const WORDS: Record<Changed, string> = { days: "the days", hours: "the hours", slots: "the slot length", holidays: "the holidays" };

/**
 * "Or just describe it": a sentence in plain words goes to /parse-hours and fills in the form. The parts that changed are
 * highlighted by the form; Undo puts everything back.
 */
export function DescribeBox({
  draft,
  onApply,
  onUndo,
}: {
  draft: ScheduleDraft;
  onApply: (next: ScheduleDraft, changed: Changed[]) => void;
  onUndo: (previous: ScheduleDraft) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ before: ScheduleDraft; changed: Changed[]; notes: string[]; confident: boolean } | null>(null);

  async function run() {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const parsed = await parseHours(text.trim());
      const { draft: next, changed } = applyParsedHours(draft, parsed);
      setResult({ before: draft, changed, notes: parsed.notes ?? [], confident: parsed.confident });
      onApply(next, changed);
    } catch (e) {
      setError(plainError(e, "Could not read that. Try the days and the times, like \"Mon to Fri 9 to 5\"."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-dashed border-border-3 bg-bg-elevated p-4">
      <label htmlFor="describe-hours" className="flex items-center gap-2 text-sm font-semibold text-text-primary">
        <Sparkles aria-hidden className="size-4 text-accent-primary" /> Or just describe it
      </label>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <textarea
          id="describe-hours"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="Mon to Sat 10 to 1 and 5 to 9, Sunday closed"
          className="min-h-[3.25rem] w-full flex-1 resize-none rounded-lg border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
        />
        <Button type="button" variant="outline" onClick={() => void run()} loading={busy} disabled={!text.trim()} disabledReason="Type the days and hours first" className="shrink-0 sm:self-start">
          Fill it in
        </Button>
      </div>
      {error && <p role="alert" className="mt-2 text-sm text-accent-error">{error}</p>}
      {result && !error && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" role="status">
          <span className="text-text-primary">
            {result.changed.length ? `Updated ${result.changed.map((c) => WORDS[c]).join(", ").replace(/, ([^,]*)$/, " and $1")}. Check it below.` : "That matches what is already set."}
          </span>
          {!result.confident && <span className="text-[#a1470d]">I was not sure about all of it, so please check the highlighted parts.</span>}
          <button
            type="button"
            onClick={() => {
              onUndo(result.before);
              setResult(null);
            }}
            className="inline-flex min-h-9 items-center gap-1 font-medium text-accent-primary hover:underline"
          >
            <Undo2 aria-hidden className="size-3.5" /> Undo
          </button>
          {result.notes.map((n) => (
            <span key={n} className="basis-full text-xs text-text-secondary">{n}</span>
          ))}
        </div>
      )}
    </div>
  );
}
