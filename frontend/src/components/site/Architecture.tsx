import { ArrowDown, ArrowRight } from "lucide-react";
import { Reveal, SplitHeading } from "./motion";
import { GNANI_STATUS } from "./submissionData";

/**
 * The call path as a diagram built from HTML, not an image of text: it stays sharp, wraps at phone width and reads with
 * a screen reader. Left to right on a wide screen, top to bottom on a narrow one. Each stage carries an honest status:
 * "Built" runs today, "Integrated" is written and tested against fakes only (docs/GNANI.md), "Being wired" is in progress.
 * No timings are shown: the old local-model numbers no longer apply, and Prisma and Timbre latency is not measured yet.
 */

type Status = "built" | "integrated" | "wiring";

const STATUS_LABEL: Record<Status, string> = {
  built: "Built",
  integrated: "Integrated, not live-tested",
  wiring: "Being wired",
};
const STATUS_CLASS: Record<Status, string> = {
  built: "vk-tag--blue",
  integrated: "vk-tag--orange",
  wiring: "vk-tag--violet",
};

interface Stage {
  key: string;
  name: string;
  api?: string;
  points: string[];
  status: Status;
  hot?: boolean;
}

const STAGES: Stage[] = [
  {
    key: "line",
    name: "Caller on a Vobiz line",
    api: "8 kHz audio over a WebSocket",
    points: ["Inbound call, or Vakya calls out", "A missed call triggers a call back (tested with fakes, not yet on a live number)", "A browser web call joins here too"],
    status: "built",
  },
  {
    key: "prisma",
    name: "Gnani Prisma v3",
    api: "wss://api.vachana.ai/stt/v3/stream",
    points: ["format=codemix, so English stays Latin", "keyterms from the business's documents", "REST per utterance on the phone today; the socket is being wired in"],
    status: "wiring",
  },
  {
    key: "turn",
    name: "Turn manager",
    points: [
      "Barge-in: the caller can talk over the agent",
      "Waits longer after the agent asks for a number or date",
      "Read-back, then commit only on a spoken yes",
      "Slot memory for this call, caller memory across calls",
    ],
    status: "built",
    hot: true,
  },
  {
    key: "llm",
    name: "Language model with tools",
    api: "Embeddings Flash (Google, hosted)",
    points: ["Answers from the business's documents (Embeddings embeddings, keyword search as backup)", "Tools: check a time, list free times, book, cancel, knowledge search, end call", "Syncs to Google Calendar when connected"],
    status: "built",
  },
  {
    key: "timbre",
    name: "Gnani Timbre",
    api: "POST /api/v1/tts/inference",
    points: ["Raw 8 kHz audio straight to the line", "Cloned voices use vachana-vc-v1 embeddings", "Indian numbers and dates spoken naturally"],
    status: "integrated",
  },
];

function Arrow() {
  return (
    <div aria-hidden className="flex h-full items-center justify-center text-[var(--s-bar)] xl:px-2">
      <ArrowDown className="h-5 w-5 xl:hidden" />
      <ArrowRight className="hidden h-5 w-5 xl:block" />
    </div>
  );
}

export function Architecture() {
  return (
    <section id="how" aria-labelledby="how-title" className="s-container mt-14 md:mt-20">
      <SplitHeading id="how-title" text="The call path," accent="stage by stage." className="h2 max-w-[18ch]" />
      <Reveal as="p" className="lede mt-5 max-w-[60ch]">
        Audio comes in at 8 kHz, Prisma turns it into text, the turn manager decides what happens next, the model answers from the business&apos;s documents and checks its schedule,
        and Timbre speaks the reply back down the same line.
      </Reveal>

      <Reveal>
        <ol
          aria-label="Call path from caller to caller"
          className="mt-10 grid grid-cols-1 gap-2 xl:grid-cols-[1fr_auto_1fr_auto_1.25fr_auto_1fr_auto_1fr] xl:gap-0"
          style={{ listStyle: "none" }}
        >
          {STAGES.flatMap((st, i) => {
            const card = (
              <li key={st.key} className="flex">
                <div className={`flex w-full flex-col rounded-2xl border p-5 ${st.hot ? "border-black bg-[var(--s-offwhite)]" : "border-[var(--s-line)] bg-white"}`}>
                  <span className={`vk-tag ${STATUS_CLASS[st.status]} self-start`}>{STATUS_LABEL[st.status]}</span>
                  <h3 className="s-item mt-3">{st.name}</h3>
                  {st.api && <p className="mt-1 break-all font-mono text-[11px] leading-snug text-[var(--violet-700)]">{st.api}</p>}
                  <ul className="mt-3 space-y-1.5 text-[13px] leading-snug text-[var(--s-ink-2)]">
                    {st.points.map((p) => (
                      <li key={p} className="ml-4 list-disc">
                        {p}
                      </li>
                    ))}
                  </ul>
                </div>
              </li>
            );
            return i === 0 ? [card] : [<li key={`a-${st.key}`} aria-hidden className="list-none"><Arrow /></li>, card];
          })}
        </ol>
      </Reveal>
      <p className="mt-3 hidden text-xs text-[var(--s-mute)] xl:block">
        Left to right: the caller&apos;s words travel Prisma, turn manager, model and Timbre, and the reply returns on the same line.
      </p>

      <Reveal className="mt-8 grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-[var(--s-line)] bg-white p-6">
          <p className="s-eyebrow">Side rail</p>
          <h3 className="s-item mt-2">Consent, label, receipt</h3>
          <p className="mt-2 text-sm leading-relaxed text-[var(--s-ink-2)]">
            Around every clip Vakya speaks: a consent tick before any voice is cloned, an AI label with a C2PA content manifest, and a signed Ed25519 receipt that anyone can
            check at /verify with no login.
          </p>
        </div>
        <div className="rounded-2xl border border-[var(--s-line)] bg-white p-6">
          <p className="s-eyebrow">Side rail</p>
          <h3 className="s-item mt-2">Live monitor and takeover</h3>
          <p className="mt-2 text-sm leading-relaxed text-[var(--s-ink-2)]">
            Beside the turn manager: a staff member sees the live transcript, and can take the call over. The agent can ask for a person but never picks the number it
            transfers to.
          </p>
        </div>
      </Reveal>

      <Reveal as="p" className="mt-6 max-w-[64ch] text-sm leading-relaxed text-[var(--s-mute)]">
        {GNANI_STATUS.long} The model that writes replies is not Gnani&apos;s: it is Embeddings Flash, hosted by Google. There is no second speech model: when Gnani is busy or down, the caller hears a
        short pre-rendered Timbre line and is offered a callback.
      </Reveal>
    </section>
  );
}

const TURN_RULES: { title: string; body: string }[] = [
  { title: "It starts early", body: "A reply begins to form after 250 ms of silence, but its audio waits for the full pause (500 ms by default), so the caller is not cut off." },
  { title: "It waits for digits", body: "After asking for a phone number or a date, it waits 1.1 s before deciding the caller is done, and longer when the answer ends in \"and\" or holds only part of a number." },
  { title: "It reads back, then commits", body: "A booking tool first answers \"not done\" with the resolved day, date and time. The agent reads them aloud and runs the tool only after a yes." },
  { title: "It remembers", body: "What the caller said (name, number, day, time) stays in a small form for the whole call. A returning caller is greeted from their profile." },
];

export function TurnRules() {
  return (
    <section id="turns" aria-labelledby="turns-title" className="s-container mt-24 md:mt-32">
      <SplitHeading id="turns-title" text="Rules that make" accent="a phone call work." className="h2 max-w-[16ch]" />
      <dl className="mt-10 grid gap-x-12 gap-y-8 border-t border-[var(--s-line)] pt-8 md:grid-cols-2">
        {TURN_RULES.map((r) => (
          <Reveal key={r.title}>
            <dt className="s-item">{r.title}</dt>
            <dd className="mt-1 text-sm leading-relaxed text-[var(--s-ink-2)]">{r.body}</dd>
          </Reveal>
        ))}
      </dl>
    </section>
  );
}
