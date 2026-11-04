"use client";

import Link from "next/link";
import { useRef } from "react";
import { ArrowRight, ArrowUpRight, EyeOff, FileCheck2, Fingerprint, Headphones, ShieldCheck, Trash2 } from "lucide-react";
import { SCRIPT_FONT, useScriptFontsNear } from "./fonts";
import { Item, Reveal, SplitHeading, Stagger } from "./motion";
import { CONDITIONS, STRESS_RUN, findRow } from "@/components/report/stressRun";
import { EXAMPLE_CALL, GNANI_MODELS, GNANI_STATUS, LANGUAGES, LIMITATIONS, REASONING, REPORT, TEAM, THEATRE_RUN } from "./submissionData";

/**
 * The landing sections after the hero and the demo video, in reading order: the call flow with a read-back transcript,
 * what is built on Gnani, the measured report card with its failures, trust, honest limits and the team.
 * Every fact comes from submissionData.ts; the report card table reads reportCardResults.json and says "pending" while it is empty.
 */

const section = "s-container mt-24 md:mt-32";

/* ------------------------------------------------------------------ stack strip */

/** Directly under the hero: what it is built on and the languages it hears, as plain tags. */
export function StackStrip() {
  return (
    <section aria-label="Built on and languages" className="s-container mt-10 md:mt-14">
      <Reveal className="flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
        <div className="flex flex-wrap justify-center gap-2">
          <span className="vk-tag vk-tag--violet">Gnani Prisma</span>
          <span className="vk-tag vk-tag--blue">Gnani Timbre</span>
        </div>
        <span aria-hidden className="hidden h-4 w-px bg-[var(--s-line)] sm:block" />
        <ul className="flex flex-wrap justify-center gap-2" aria-label="Languages">
          {LANGUAGES.map((l) => (
            <li key={l} className="vk-tag vk-tag--orange">
              {l}
            </li>
          ))}
        </ul>
      </Reveal>
    </section>
  );
}

/* ------------------------------------------------------------------ the call, in four moves */

const STEPS: { title: string; body: string }[] = [
  {
    title: "It answers first",
    body: "A caller rings the business's number, a clinic's, a hotel's or a salon's, or gives a missed call and Vakya rings back. The agent speaks first and says it is an AI.",
  },
  {
    title: "It listens and answers",
    body: "Hindi, Marathi, English and the mix of all three. Answers come from the business's own documents and its schedule, not from the model's memory.",
  },
  {
    title: "It reads everything back",
    body: "Day, date, time and phone number are read aloud. The booking is made only after the caller says yes, and a wrong digit is fixed on the spot.",
  },
  {
    title: "It leaves a signed receipt",
    body: "Every clip it speaks gets a signed receipt anyone can check at /verify. A person can watch the call live and take over.",
  },
];

/** An invented example call, drawn in HTML so it never goes stale like a screenshot. */
function CallTranscript() {
  const ref = useRef<HTMLDivElement>(null);
  // Devanagari text: the face downloads only as the frame nears the screen (see fonts.ts).
  const near = useScriptFontsNear(ref);
  const mr = near ? SCRIPT_FONT.mr : "";
  return (
    <figure ref={ref} className="overflow-hidden rounded-2xl border border-[var(--s-line)] bg-white" aria-label="Example call: a Marathi caller books an appointment and corrects one digit">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-[var(--s-line)] px-5 py-3 sm:px-6">
        <p className="text-sm font-semibold text-black">Sunrise Clinic (invented)</p>
        <div className="flex gap-2">
          <span className="vk-tag vk-tag--blue">Example call</span>
          <span className="vk-tag vk-tag--violet">AI voice</span>
        </div>
      </div>
      <ol className="space-y-3 px-5 py-5 sm:px-6">
        {EXAMPLE_CALL.map((l, i) => (
          <li key={i} className={l.who === "caller" ? "flex justify-end" : "flex"}>
            <div className={`max-w-[92%] rounded-lg px-4 py-3 sm:max-w-[88%] ${l.who === "caller" ? "bg-[var(--violet-200)]" : "bg-[#f8f8f8]"}`}>
              <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[1px] text-black">
                {l.who === "agent" ? "Agent" : "Caller"}
                {l.tag && <span className="rounded bg-white px-1.5 py-0.5 text-[10px] tracking-[0.5px] text-[var(--violet-700)]">{l.tag}</span>}
              </p>
              <p lang="mr" className={`${mr} mt-1 text-[15px] leading-snug text-black`}>
                {l.text}
              </p>
              <p className="mt-1 text-xs leading-snug text-[var(--s-mute)]">{l.gloss}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="border-t border-[var(--s-line)] px-5 py-3 text-xs leading-relaxed text-[var(--s-mute)] sm:px-6">
        Written by us to show the read-back. The clinic, the caller and the number are made up; it is not a recording.
      </p>
    </figure>
  );
}

export function CallFlow() {
  return (
    <section id="call" aria-labelledby="call-title" className={section}>
      <div className="grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:gap-16">
        <div>
          <SplitHeading id="call-title" text="One call," accent="four moves." className="h2 max-w-[14ch]" />
          <Reveal as="p" className="lede mt-5 max-w-[44ch]">
            The read-back is the product. Phone lines are noisy and digits get misheard, so Vakya says what it understood and waits for a yes.
          </Reveal>
          <Stagger as="ol" className="mt-10 divide-y divide-[var(--s-line)] border-y border-[var(--s-line)]">
            {STEPS.map((s, i) => (
              <Item as="li" key={s.title} className="grid grid-cols-[2.25rem_1fr] gap-x-2 py-5">
                <span className="s-item tabular-nums text-[var(--violet-700)]">{i + 1}</span>
                <div>
                  <h3 className="s-item">{s.title}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-[var(--s-mute)]">{s.body}</p>
                </div>
              </Item>
            ))}
          </Stagger>
          <Reveal className="mt-8 flex flex-wrap gap-3">
            <Link href="/how-it-works" className="s-btn s-btn--secondary">
              How it works
              <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          </Reveal>
        </div>
        <Reveal scale>
          <CallTranscript />
        </Reveal>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ built on Gnani */

export function BuiltOnGnani() {
  return (
    <section id="gnani" aria-labelledby="gnani-title" className={section}>
      <Reveal>
        <div className="s-night-panel on-night rounded-2xl px-6 py-12 sm:px-10 md:px-14 md:py-16">
          <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-14">
            <div>
              <p className="s-eyebrow">Built on Gnani</p>
              <SplitHeading id="gnani-title" text="The speech is" accent="Gnani's." className="h2 mt-4 max-w-[12ch] !text-white" />
              <p className="mt-5 max-w-[40ch] text-base leading-relaxed text-[var(--s-night-mute)]">
                Vakya calls Gnani&apos;s API directly. Nothing sits in between except the turn manager that decides when to speak and when to confirm.
              </p>
              <p className="mt-6 inline-flex rounded-lg border border-[var(--s-night-line)] px-3 py-2 text-sm font-semibold text-white">{GNANI_STATUS.short}</p>
              <p className="mt-3 max-w-[40ch] text-sm leading-relaxed text-[var(--s-night-mute)]">{GNANI_STATUS.long}</p>
            </div>
            <ul className="divide-y divide-[var(--s-night-line)] border-y border-[var(--s-night-line)]">
              {GNANI_MODELS.map((m) => (
                <li key={m.id} className="py-6">
                  <h3 className="s-h3 !text-white">{m.name}</h3>
                  <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-[var(--violet-400)]">
                    {m.api.map((a) => (
                      <code key={a} className="break-all">
                        {a}
                      </code>
                    ))}
                  </p>
                  <p className="mt-3 text-sm leading-relaxed text-[var(--s-night-mute)]">{m.role}</p>
                </li>
              ))}
              <li className="py-6">
                <h3 className="s-h3 !text-white">Reasoning</h3>
                <p className="mt-3 text-sm leading-relaxed text-[var(--s-night-mute)]">
                  {REASONING.llm} {REASONING.whenBusy}
                </p>
              </li>
            </ul>
          </div>
        </div>
      </Reveal>
    </section>
  );
}

/* ------------------------------------------------------------------ the report card, with its failures */

const CARD_ROWS = CONDITIONS.filter((c) => ["clean", "tel8k", "g711", "noise_5db", "tel8k+noise_5db", "noise_0db"].includes(c.id));

export function ReportTeaser() {
  return (
    <section id="measured" aria-labelledby="measured-title" className={section}>
      <Reveal className="grid grid-cols-1 overflow-hidden rounded-2xl border border-[var(--s-line)] bg-white lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="flex flex-col p-8 md:p-10">
          <p className="s-stat tabular-nums" style={{ fontSize: "clamp(2.25rem, 5vw, 3.25rem)" }}>
            {REPORT.measured ? "Report card" : "Pending"}
          </p>
          <span className="vk-tag vk-tag--orange mt-4 self-start">{REPORT.measured ? "Measured on Gnani Prisma" : "Gnani Prisma run pending"}</span>
          <h2 id="measured-title" className="mt-4 font-[family-name:var(--s-font-display)] text-[clamp(1.75rem,3vw,2.25rem)] leading-[1.1] text-black">
            The report card will show where it breaks
          </h2>
          <p className="mt-4 max-w-[46ch] text-base leading-relaxed text-[var(--s-ink-2)]">
            {`The same ${REPORT.clips} invented booking calls are played clean, over an 8 kHz phone line, through street noise and a phone codec, with and without the business's own names as keyterms, and scored on whether the name, day and time all came through.`}
          </p>
          <p className="mt-3 max-w-[46ch] text-sm leading-relaxed text-[var(--s-mute)]">
            We publish no number until Gnani Prisma has heard them. The table fills in from the run&apos;s result file; until then every cell says pending.
          </p>
          <div className="mt-auto flex flex-wrap gap-3 pt-8">
            <Link href="/report-card" className="s-btn s-btn--primary">
              Read the method
              <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          </div>
        </div>
        <div className="border-t border-[var(--s-line)] lg:border-l lg:border-t-0">
          <div className="overflow-x-auto">
            <table className="vk-table min-w-[26rem] text-sm">
              <caption className="sr-only">Share of booking calls completed under each line condition, without and with keyterms</caption>
              <thead>
                <tr>
                  <th scope="col">Line condition</th>
                  <th scope="col">No keyterms</th>
                  <th scope="col">With keyterms</th>
                </tr>
              </thead>
              <tbody>
                {CARD_ROWS.map((c) => {
                  const plain = findRow(STRESS_RUN, c.id, false);
                  const keyed = findRow(STRESS_RUN, c.id, true);
                  return (
                    <tr key={c.id}>
                      <th scope="row">{c.name}</th>
                      <td className="text-[var(--s-ink-2)]">{plain ? `${Math.round(plain.taskCompletion)}%` : <span className="text-[var(--s-mute)]">pending</span>}</td>
                      <td className="text-[var(--s-ink-2)]">{keyed ? `${Math.round(keyed.taskCompletion)}%` : <span className="text-[var(--s-mute)]">pending</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="border-t border-[var(--s-line)] px-4 py-3 text-xs leading-relaxed text-[var(--s-mute)]">
            The audio is synthetic and the noise is added by software: repeat on real recordings before relying on a number. The only run so far is Caller Theatre: {THEATRE_RUN.passed} of {THEATRE_RUN.of} scenarios pass, on a scripted model.
          </p>
        </div>
      </Reveal>
    </section>
  );
}

/* ------------------------------------------------------------------ trust */

const TRUST: { icon: typeof ShieldCheck; title: string; body: string }[] = [
  { icon: Headphones, title: "It says it is an AI", body: "Every call opens with a spoken disclosure, before anything else the caller hears." },
  { icon: ShieldCheck, title: "Cloned voices need consent", body: "The owner ticks a consent statement before a voice is cloned. The record is stored with the voice, and withdrawing it deletes both." },
  { icon: Fingerprint, title: "A label and a content manifest", body: "Every clip has an AI-generated label and a C2PA content manifest in the file, so a copy can be traced back to its receipt. It is evidence, not proof: a re-encoded or edited clip no longer matches." },
  { icon: FileCheck2, title: "A signed receipt", body: "Each clip gets an Ed25519 receipt. Anyone can drop a clip on /verify, no login, and see whether Vakya made it." },
  { icon: Trash2, title: "Callers can be forgotten", body: "A returning caller's profile is kept to serve their next call, expires after a set time, and can be deleted by number." },
  { icon: EyeOff, title: "A person can step in", body: "A staff member can watch a live call and take it over. The agent never chooses the number it transfers to." },
];

export function TrustGrid() {
  return (
    <section id="trust" aria-labelledby="trust-title" className={section}>
      <SplitHeading id="trust-title" text="Built to be" accent="checked." className="h2 max-w-[14ch]" />
      <Reveal as="p" className="lede mt-5 max-w-[52ch]">
        A voice that sounds human on a phone line needs a paper trail. These are the parts that exist today.
      </Reveal>
      <Stagger as="ul" className="mt-10 grid gap-x-10 gap-y-8 border-t border-[var(--s-line)] pt-8 sm:grid-cols-2 lg:grid-cols-3">
        {TRUST.map((t) => (
          <Item as="li" key={t.title} className="flex gap-4">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-[var(--violet-200)] text-black">
              <t.icon className="h-5 w-5" aria-hidden />
            </span>
            <div>
              <h3 className="s-item">{t.title}</h3>
              <p className="mt-1 text-sm leading-relaxed text-[var(--s-mute)]">{t.body}</p>
            </div>
          </Item>
        ))}
      </Stagger>
      <Reveal className="mt-8 flex flex-wrap gap-3">
        <Link href="/verify" className="s-btn s-btn--secondary">
          Verify a clip
          <ArrowUpRight className="h-4 w-4" aria-hidden />
        </Link>
      </Reveal>
    </section>
  );
}

/* ------------------------------------------------------------------ honest limits */

export function Limitations() {
  return (
    <section id="limits" aria-labelledby="limits-title" className={section}>
      <div className="rounded-2xl border border-[var(--s-line)] bg-[var(--s-offwhite)] px-6 py-12 sm:px-10 md:px-14 md:py-16">
        <SplitHeading id="limits-title" text="What it does not" accent="do yet." className="h2 max-w-[14ch]" />
        <Reveal as="p" className="lede mt-5 max-w-[56ch]">
          Gnani asks for honest limits, so they are on the front page and not in a footnote.
        </Reveal>
        <Stagger as="ul" className="mt-10 grid gap-x-12 gap-y-7 md:grid-cols-2">
          {LIMITATIONS.map((l) => (
            <Item as="li" key={l.title}>
              <h3 className="s-item">{l.title}</h3>
              <p className="mt-1 text-sm leading-relaxed text-[var(--s-ink-2)]">{l.body}</p>
            </Item>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ team */

export function Team() {
  return (
    <section id="team" aria-labelledby="team-title" className={section}>
      <div className="grid grid-cols-1 gap-8 border-t border-[var(--s-line)] pt-10 md:grid-cols-[minmax(0,0.6fr)_minmax(0,1.4fr)]">
        <h2 id="team-title" className="s-h3">
          Team
        </h2>
        <div>
          <ul className="grid gap-6 sm:grid-cols-2">
            {TEAM.map((m) => (
              <li key={m.name}>
                <p className={`s-item ${m.tbc ? "text-[var(--s-mute)]" : ""}`}>{m.name}</p>
                <p className="mt-1 text-sm text-[var(--s-mute)]">{m.role}</p>
              </li>
            ))}
          </ul>
          <p className="mt-6 max-w-[56ch] text-sm leading-relaxed text-[var(--s-mute)]">
            Built for the Great Indian AI Internship Challenge by Gnani AI. Vakya is a student project and is not affiliated with Gnani AI. Every booking, caller and number on this site is invented.
          </p>
        </div>
      </div>
    </section>
  );
}
