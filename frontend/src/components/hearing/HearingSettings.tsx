"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import { Plus, Trash2, X } from "lucide-react";
import { Button, Card, Skeleton } from "@/components/ui";
import { FrontDeskSelect } from "@/components/schedules/FrontDeskSelect";
import { useAgents } from "@/hooks/useApi";
import { formatDate } from "@/lib/formatDate";
import { cn } from "@/lib/utils";
import { addTerm, deleteRule, deleteTerm, errorText, oldestUnused, teach, useHearing } from "./hearingApi";
import { inputClass } from "./Misheard";
import type { HearingRule } from "./types";

function Source({ rule }: { rule: HearingRule }) {
  const from = rule.learnt_from;
  if (from !== "owner" && from?.conversation_id) {
    return (
      <Link href={`/desk/calls?call=${encodeURIComponent(from.conversation_id)}`} className="font-medium text-accent-primary underline-offset-2 hover:underline">
        Learnt from a call
      </Link>
    );
  }
  return <span>Added by you</span>;
}

/** The meter: "7 of 10 corrections used". */
function Meter({ used, limit }: { used: number; limit: number }) {
  const pct = Math.min(100, Math.round((used / Math.max(1, limit)) * 100));
  return (
    <div>
      <p className="text-sm font-medium text-text-primary" data-testid="hearing-counter">{used} of {limit} corrections used</p>
      <div role="progressbar" aria-label="Corrections used" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used} className="mt-2 h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-border">
        <div className={cn("h-full rounded-full", used >= limit ? "bg-accent-warning" : "bg-accent-primary")} style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-2 text-xs text-text-secondary">
        Gnani&apos;s Prisma accepts at most {limit} corrections for every request, so each front desk keeps {limit}. When they are all used, replace the one that has gone longest without helping.
      </p>
    </div>
  );
}

/** Settings → Hearing: what each front desk has learnt to hear. The corrections (heard → meant, where from, when), a way to add one by
 *  hand, and the words callers say at this desk (sent to Prisma as keyterms). */
export function HearingSettings() {
  const agents = useAgents();
  const list = useMemo(() => agents.data ?? [], [agents.data]);
  const [picked, setPicked] = useState<string>("");
  const agentId = picked && list.some((a) => a.id === picked) ? picked : (list[0]?.id ?? "");
  const hearing = useHearing(agentId);
  const data = hearing.data;
  const [heard, setHeard] = useState("");
  const [meant, setMeant] = useState("");
  const [term, setTerm] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<{ text: string; evict: boolean } | null>(null);
  const [termProblem, setTermProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    setProblem(null);
    setTermProblem(null);
    setSaved(null);
    setHeard("");
    setMeant("");
    setTerm("");
  }, [agentId]);

  async function addRule(e?: FormEvent, evict = false) {
    e?.preventDefault();
    if (!agentId || busy) return;
    if (!heard.trim() || !meant.trim()) {
      setProblem({ text: "Fill in both: what Vakya hears and what it should be.", evict: false });
      return;
    }
    setBusy("rule");
    setProblem(null);
    setSaved(null);
    try {
      const res = await teach(agentId, { heard, meant, evict_oldest_unused: evict });
      setSaved(res.message);
      setHeard("");
      setMeant("");
    } catch (err) {
      setProblem({ text: errorText(err, "Could not save that. Try again in a moment."), evict: !!oldestUnused(err) });
    } finally {
      setBusy(null);
    }
  }

  async function removeRule(rule: HearingRule) {
    setBusy(rule.id);
    setProblem(null);
    setSaved(null);
    try {
      await deleteRule(agentId, rule.id);
    } catch (err) {
      setProblem({ text: errorText(err, "Could not delete that. Try again in a moment."), evict: false });
    } finally {
      setBusy(null);
    }
  }

  async function addWord(e: FormEvent) {
    e.preventDefault();
    if (!agentId || busy || !term.trim()) return;
    setBusy("term");
    setTermProblem(null);
    try {
      await addTerm(agentId, term);
      setTerm("");
    } catch (err) {
      setTermProblem(errorText(err, "Could not add that word. Try again in a moment."));
    } finally {
      setBusy(null);
    }
  }

  async function removeWord(t: string) {
    setBusy(`term:${t}`);
    setTermProblem(null);
    try {
      await deleteTerm(agentId, t);
    } catch (err) {
      setTermProblem(errorText(err, "Could not remove that word. Try again in a moment."));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card aria-labelledby="hearing" className="space-y-5">
      <div className="border-b border-border pb-4">
        <h2 id="hearing" className="font-display text-lg font-semibold text-text-primary">Hearing</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Teach each front desk how its callers sound. Correct a line in a call (&ldquo;Misheard?&rdquo; on the Calls page) or add a correction here; Vakya applies it on every later call and listens for the right words.
        </p>
      </div>

      {agents.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : list.length === 0 ? (
        <p className="text-sm text-text-secondary">There is no front desk yet. Make one on the Front desk page, then teach it here.</p>
      ) : (
        <>
          {list.length > 1 && (
            <FrontDeskSelect agents={list} value={agentId} onChange={setPicked} allowAll={false} label="Front desk" size="field" className="max-w-sm" />
          )}

          {hearing.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : hearing.error || !data ? (
            <p role="alert" className="text-sm text-accent-error">Could not load what this front desk has learnt. Try again in a moment.</p>
          ) : (
            <>
              <Meter used={data.rules_used} limit={data.rule_limit} />

              <section aria-label="Corrections" className="space-y-3">
                <h3 className="text-sm font-semibold text-text-primary">Corrections</h3>
                {data.rules.length === 0 ? (
                  <p className="text-sm text-text-secondary">Nothing yet. When Vakya mishears a word, open the call and tap &ldquo;Misheard?&rdquo; under the line.</p>
                ) : (
                  <ul className="divide-y divide-border rounded-xl border border-border bg-bg-surface">
                    {data.rules.map((r) => (
                      <li key={r.id} className="flex items-start gap-3 p-3.5">
                        <div className="min-w-0 flex-1">
                          <p className="break-words text-[15px] text-text-primary">
                            <span className="text-text-secondary">{r.heard}</span>
                            <span aria-hidden className="px-1.5 text-text-muted">→</span>
                            <span className="sr-only"> becomes </span>
                            <span className="font-medium">{r.meant}</span>
                          </p>
                          <p className="mt-0.5 text-xs text-text-secondary">
                            <Source rule={r} />
                            {r.created_at ? ` · ${formatDate(r.created_at)}` : ""}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => void removeRule(r)}
                          disabled={busy === r.id}
                          aria-label={`Delete the correction ${r.heard} to ${r.meant}`}
                          className="grid size-10 shrink-0 place-items-center rounded-lg text-text-secondary hover:bg-bg-base hover:text-accent-error focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary disabled:opacity-50"
                        >
                          <Trash2 aria-hidden className="size-4" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                <form onSubmit={(e) => void addRule(e)} aria-label="Add a correction" className="space-y-3 rounded-xl border border-dashed border-border p-3.5">
                  <p className="text-sm font-medium text-text-primary">Add one yourself</p>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block text-xs font-semibold text-text-secondary">
                      What Vakya hears
                      <input value={heard} onChange={(e) => setHeard(e.target.value)} maxLength={60} placeholder="o p d" className={cn(inputClass, "mt-1 font-normal")} />
                    </label>
                    <label className="block text-xs font-semibold text-text-secondary">
                      What it should be
                      <input value={meant} onChange={(e) => setMeant(e.target.value)} maxLength={80} placeholder="OPD" className={cn(inputClass, "mt-1 font-normal")} />
                    </label>
                  </div>
                  {problem && (
                    <div role="alert" className="space-y-2 text-sm text-accent-error">
                      <p>{problem.text}</p>
                      {problem.evict && (
                        <Button type="button" size="sm" variant="outline" loading={busy === "rule"} onClick={() => void addRule(undefined, true)}>
                          Replace the oldest unused one
                        </Button>
                      )}
                    </div>
                  )}
                  {saved && <p role="status" className="text-sm text-accent-success">{saved}</p>}
                  <Button type="submit" size="sm" loading={busy === "rule"} leftIcon={<Plus className="size-4" />}>
                    Add correction
                  </Button>
                </form>
              </section>

              <section aria-label="Vocabulary" className="space-y-3">
                <div>
                  <h3 className="text-sm font-semibold text-text-primary">Words callers say at this desk</h3>
                  <p className="mt-0.5 text-xs text-text-secondary">Names, places and terms Prisma should favour. Each correction adds the right words here. {data.vocabulary.length} of {data.vocabulary_limit} used.</p>
                </div>
                {data.vocabulary.length === 0 ? (
                  <p className="text-sm text-text-secondary">No words yet.</p>
                ) : (
                  <ul className="flex flex-wrap gap-2">
                    {data.vocabulary.map((v) => (
                      <li key={v.term} className="inline-flex items-center gap-1 rounded-full border border-border bg-bg-surface py-1 pl-3 pr-1 text-sm text-text-primary">
                        {v.term}
                        <button
                          type="button"
                          onClick={() => void removeWord(v.term)}
                          disabled={busy === `term:${v.term}`}
                          aria-label={`Remove ${v.term} from the vocabulary`}
                          className="grid size-7 place-items-center rounded-full text-text-secondary hover:bg-bg-base hover:text-text-primary focus-visible:outline-2 focus-visible:outline-accent-primary"
                        >
                          <X aria-hidden className="size-3.5" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <form onSubmit={(e) => void addWord(e)} aria-label="Add a word" className="flex flex-wrap items-center gap-2">
                  <input value={term} onChange={(e) => setTerm(e.target.value)} maxLength={80} aria-label="A word or name callers say" placeholder="Shivajinagar" className={cn(inputClass, "max-w-xs flex-1")} />
                  <Button type="submit" size="sm" variant="outline" loading={busy === "term"} disabled={!term.trim()}>Add word</Button>
                </form>
                {termProblem && <p role="alert" className="text-sm text-accent-error">{termProblem}</p>}
              </section>
            </>
          )}
        </>
      )}
    </Card>
  );
}
