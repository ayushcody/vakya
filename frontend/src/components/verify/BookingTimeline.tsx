"use client";

import { useEffect, useRef, useState } from "react";
import { Check, CheckCheck, Loader2, MessageSquareQuote, Pause, Play, ShieldCheck, ShieldAlert, UserRound, Volume2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BookingEvidence, EvidenceItem } from "./types";

/**
 * The proof of a booking as four steps: asked (what the caller was asked for), said (what each detail came from), read back
 * (the agent repeated it) and confirmed (the caller said yes). Used by the owner's booking drawer and the public /verify page.
 * `audioUrl(item)` returns where to fetch a step's recording, or null when there is none to play; `fetchAudio` turns that into
 * a playable URL (the owner's drawer needs the sign-in header, the public page does not).
 */
export interface TimelineProps {
  evidence: BookingEvidence;
  audioUrl: (item: EvidenceItem) => string | null;
  fetchAudio?: (url: string) => Promise<string>;
  className?: string;
  /** Show the hashes ("seals") of each step and of the receipt. The public page shows them; the owner's drawer hides them until asked. */
  seals?: boolean;
}

const STEP_TITLE = { said: "Said", readback: "Read back", confirm: "Confirmed" } as const;
const BASIS: Record<string, string> = { heard: "taken from these words", text: "found in these words", inferred: "best guess: the first thing asked" };

function short(hash: string | null): string {
  return hash ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : "none";
}

export function PlayButton({ label, url, fetchAudio }: { label: string; url: string; fetchAudio?: (u: string) => Promise<string> }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "playing" | "error">("idle");
  useEffect(() => () => audio.current?.pause(), []);
  async function toggle() {
    if (state === "playing") {
      audio.current?.pause();
      setState("idle");
      return;
    }
    setState("loading");
    try {
      if (!audio.current) {
        const src = fetchAudio ? await fetchAudio(url) : url;
        const a = new Audio(src);
        a.onended = () => setState("idle");
        a.onerror = () => setState("error");
        audio.current = a;
      }
      await audio.current.play();
      setState("playing");
    } catch {
      setState("error");
    }
  }
  return (
    <button
      type="button"
      onClick={() => void toggle()}
      aria-label={state === "playing" ? `Pause ${label}` : `Play ${label}`}
      className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-border bg-bg-surface px-3 text-sm font-medium text-text-primary hover:bg-bg-base focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
    >
      {state === "loading" ? <Loader2 aria-hidden className="size-4 animate-spin" /> : state === "playing" ? <Pause aria-hidden className="size-4" /> : <Play aria-hidden className="size-4" />}
      {state === "error" ? "Could not play" : state === "playing" ? "Pause" : "Play"}
    </button>
  );
}

function Step({ title, icon, children, last }: { title: string; icon: React.ReactNode; children: React.ReactNode; last?: boolean }) {
  return (
    <li className="relative flex gap-3.5 pb-6 last:pb-0">
      {!last && <span aria-hidden className="absolute left-[17px] top-9 bottom-0 w-px bg-border" />}
      <span aria-hidden className="relative z-[1] grid size-9 shrink-0 place-items-center rounded-full border border-border bg-bg-surface text-text-secondary">{icon}</span>
      <div className="min-w-0 flex-1 pt-1">
        <h4 className="text-[13px] font-semibold uppercase tracking-[1px] text-text-secondary">{title}</h4>
        <div className="mt-1.5 space-y-2.5">{children}</div>
      </div>
    </li>
  );
}

function Quote({ item, url, who, fetchAudio, speaker, seals = true }: { item: EvidenceItem; url: string | null; who: string; fetchAudio?: (u: string) => Promise<string>; speaker: string; seals?: boolean }) {
  return (
    <div className="rounded-xl border border-border bg-bg-surface p-3.5">
      <p className="flex flex-wrap items-center gap-x-2 text-xs text-text-secondary">
        <span className="font-medium text-text-primary">{who}</span>
        {item.label && <span>{item.label}</span>}
        {item.basis && <span>· {BASIS[item.basis]}</span>}
        {item.typed && <span>· typed</span>}
      </p>
      {item.transcript ? (
        <p lang={item.language === "hi" || item.language === "mr" ? item.language : undefined} className="mt-1.5 text-[17px] leading-snug text-text-primary">
          “{item.transcript}”
        </p>
      ) : (
        <p className="mt-1.5 text-sm text-text-secondary">{item.transcript_sha256 && !item.has_audio ? "Nothing was said for this detail." : "The words were deleted when the retention period ended."}</p>
      )}
      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        {url ? <PlayButton label={`${speaker} recording`} url={url} fetchAudio={fetchAudio} /> : item.has_audio ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-text-secondary"><Volume2 aria-hidden className="size-3.5" />Recording kept, hidden from the public</span>
        ) : null}
        {seals && (
          <span className="font-mono text-[11px] text-text-secondary" title={`Transcript ${item.transcript_sha256}\nAudio ${item.audio_sha256 ?? "none"}`}>
            words {short(item.transcript_sha256)}{item.audio_sha256 ? ` · audio ${short(item.audio_sha256)}` : ""}
          </span>
        )}
      </div>
    </div>
  );
}

export function BookingTimeline({ evidence, audioUrl, fetchAudio, className, seals = true }: TimelineProps) {
  const fields = evidence.items.filter((i) => i.role === "field");
  const readback = evidence.items.find((i) => i.role === "readback");
  const confirm = evidence.items.find((i) => i.role === "confirm");
  // One utterance often gives several details ("I am Priya, 98765..."): it is shown once, with every detail it gave.
  const saidGroups: EvidenceItem[] = [];
  for (const f of fields) {
    if (!f.turn_id) continue;
    const g = saidGroups.find((x) => x.turn_id === f.turn_id);
    if (g) g.label = [g.label, f.label].filter(Boolean).join(", ");
    else saidGroups.push({ ...f });
  }
  const asked = fields.filter((f) => f.label).map((f) => f.label as string);
  return (
    <section aria-label="Proof of this booking" className={className}>
      <div
        className={cn(
          "flex items-start gap-3 rounded-xl border p-3.5",
          evidence.verified ? "border-[color-mix(in_srgb,var(--color-accent-success)_40%,transparent)] bg-success-tint" : "border-[color-mix(in_srgb,var(--color-accent-error)_40%,transparent)] bg-error-tint"
        )}
        role="status"
      >
        {evidence.verified ? <ShieldCheck aria-hidden className="mt-0.5 size-5 shrink-0 text-accent-success" /> : <ShieldAlert aria-hidden className="mt-0.5 size-5 shrink-0 text-accent-error" />}
        <div className="min-w-0">
          <p className="text-[15px] font-semibold text-text-primary">{evidence.verified ? "Receipt verified" : "This proof does not verify"}</p>
          <p className="mt-0.5 text-sm text-text-secondary">
            {evidence.verified
              ? "Every transcript and recording below matches its hash, and the signed receipt covers all of them."
              : evidence.verification.reason === "tampered"
                ? "Something stored for this booking was changed after it was sealed. Do not rely on it."
                : "The signed receipt that covers this proof could not be verified."}
          </p>
          {!evidence.complete && <p className="mt-1 text-sm text-text-secondary">Not every detail could be traced to a caller utterance or a read-back; the steps below show what was found.</p>}
          {evidence.deleted && <p className="mt-1 text-sm text-text-secondary">The words and recordings were deleted after {evidence.retention_days} days. The hashes stay, so a copy somebody holds can still be checked.</p>}
        </div>
      </div>

      <ol className="mt-5">
        <Step title="Asked" icon={<MessageSquareQuote className="size-4" />}>
          <p className="text-sm text-text-secondary">{asked.length ? `The front desk needed: ${asked.join(", ").toLowerCase()}.` : "The front desk collected the details of the booking."}</p>
        </Step>
        <Step title={STEP_TITLE.said} icon={<UserRound className="size-4" />}>
          {saidGroups.map((g) => (
            <Quote key={g.id} item={g} who="Caller" speaker="caller" url={audioUrl(g)} fetchAudio={fetchAudio} seals={seals} />
          ))}
          {fields.some((f) => !f.turn_id) && (
            <p className="text-sm text-text-secondary">Not found in what the caller said: {fields.filter((f) => !f.turn_id).map((f) => (f.label ?? f.field ?? "").toLowerCase()).join(", ")}.</p>
          )}
        </Step>
        <Step title={STEP_TITLE.readback} icon={<Check className="size-4" />}>
          {readback ? <Quote item={readback} who="Front desk" speaker="front desk" url={audioUrl(readback)} fetchAudio={fetchAudio} seals={seals} /> : <p className="text-sm text-text-secondary">No read-back was found before the booking.</p>}
        </Step>
        <Step title={STEP_TITLE.confirm} icon={<CheckCheck className="size-4" />} last>
          {confirm ? <Quote item={confirm} who="Caller" speaker="caller" url={audioUrl(confirm)} fetchAudio={fetchAudio} seals={seals} /> : null}
        </Step>
      </ol>
      {seals && <p className="mt-4 break-all font-mono text-[11px] text-text-secondary">Receipt {evidence.receipt_id} · seal {short(evidence.manifest_sha256)}</p>}
    </section>
  );
}
