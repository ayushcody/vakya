"use client";

import { useId, useRef, useState, type FormEvent } from "react";
import { Check, EarOff } from "lucide-react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { errorText, oldestUnused, teach } from "./hearingApi";

export const inputClass =
  "h-11 w-full min-w-0 rounded-lg border border-border bg-bg-surface px-3 text-[15px] text-text-primary placeholder:text-text-muted focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary";

/** The words the owner has selected inside `text` (so a click on "Misheard?" starts with them), else nothing. */
function selectedWithin(text: string): string {
  try {
    const s = (window.getSelection?.()?.toString() ?? "").replace(/\s+/g, " ").trim();
    return s && s.length <= 60 && text.includes(s) ? s : "";
  } catch {
    return "";
  }
}

/**
 * "Misheard?" under a caller's line: select or type the words Vakya got wrong, type what was meant, "Teach Vakya". The desk
 * then listens for those words on every later call (a correction rule for Gnani's Prisma plus the right words as keyterms).
 * `turnIndex` is the line's place in the call's transcript; a live call (no transcript yet) leaves it out.
 */
export function Misheard({ agentId, conversationId, turnIndex, text, className }: {
  agentId: string;
  conversationId: string;
  turnIndex?: number;
  text: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [heard, setHeard] = useState("");
  const [meant, setMeant] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [full, setFull] = useState<{ id: string; heard: string; meant: string } | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const picked = useRef("");
  const id = useId();
  const heardRef = useRef<HTMLInputElement>(null);

  function openForm() {
    if (open) {
      setOpen(false);
      return;
    }
    setHeard(picked.current || selectedWithin(text));
    setMeant("");
    setError(null);
    setFull(null);
    setDone(null);
    setOpen(true);
    setTimeout(() => heardRef.current?.focus(), 0);
  }

  async function submit(e?: FormEvent, evict = false) {
    e?.preventDefault();
    if (busy) return;
    if (!heard.trim() || !meant.trim()) {
      setError("Fill in both: what Vakya heard and what was meant.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await teach(agentId, { heard, meant, conversation_id: conversationId, turn_index: turnIndex, evict_oldest_unused: evict });
      setDone(res.message);
      setFull(null);
      setOpen(false);
    } catch (err) {
      setFull(oldestUnused(err));
      setError(errorText(err, "Could not save that. Try again in a moment."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={cn("mt-1.5", className)}>
      {done && (
        <p role="status" className="mb-1 flex items-start gap-1.5 text-xs text-accent-success">
          <Check aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <span>{done}</span>
        </p>
      )}
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onPointerDown={() => { picked.current = selectedWithin(text); }}
        onClick={openForm}
        className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-1.5 text-xs font-medium text-accent-primary underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
      >
        <EarOff aria-hidden className="size-3.5" />
        Misheard?
      </button>
      {open && (
        <form id={id} onSubmit={(e) => void submit(e)} aria-label="Teach Vakya what was meant" className="mt-2 space-y-3 rounded-xl border border-border bg-bg-surface p-3.5 text-left">
          <p className="text-xs text-text-secondary">Select the words Vakya got wrong in the line above, or type them. Vakya will then listen for the right words on this front desk.</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-xs font-semibold text-text-secondary">
              What Vakya heard
              <input ref={heardRef} value={heard} onChange={(e) => setHeard(e.target.value)} maxLength={60} placeholder="Dr. Call Karni" className={cn(inputClass, "mt-1 font-normal")} />
            </label>
            <label className="block text-xs font-semibold text-text-secondary">
              What was meant
              <input value={meant} onChange={(e) => setMeant(e.target.value)} maxLength={80} placeholder="Dr. Kulkarni" className={cn(inputClass, "mt-1 font-normal")} />
            </label>
          </div>
          {error && (
            <div role="alert" className="space-y-2 text-sm text-accent-error">
              <p>{error}</p>
              {full && (
                <Button type="button" size="sm" variant="outline" loading={busy} onClick={() => void submit(undefined, true)}>
                  Replace the oldest unused one ({full.heard} → {full.meant})
                </Button>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" loading={busy}>Teach Vakya</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </form>
      )}
    </div>
  );
}
