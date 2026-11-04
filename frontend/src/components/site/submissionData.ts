/**
 * Every fact the public pages and the README quote, in one place, so that when something changes (the Gnani key
 * arrives and Prisma is measured, the demo video is recorded, the team is confirmed) one file is edited.
 *
 * The acoustic-stress table lives in components/report/reportCardResults.json and is empty until Gnani Prisma has been
 * run: the landing page and the report card read it and say "pending" while it is. docs/SUBMISSION.md and README.md repeat
 * these values by hand: update them when this file changes.
 *
 * Status honesty: nothing here claims users, customers, revenue, partnerships, government APIs or real calls.
 */
import { CONDITIONS, STRESS_RUN } from "@/components/report/stressRun";

/* ------------------------------------------------------------------ links and dates */

export const SUBMISSION = {
  /** Challenge deadline, 11:59 PM IST. */
  deadline: "10 Nov 2026",
  /** When the 60-second demo video lands (placeholder text on the site until `public/landing/demo.mp4` exists). */
  demoVideoDate: "9 Nov",
  demoVideoSrc: "/landing/demo.mp4",
  demoVideoPoster: "/landing/demo-poster.jpg",
  /** Filled in by a human: the nominated post links. */
  linkedinPost: "TBC",
  xPost: "TBC",
} as const;

/* ------------------------------------------------------------------ Gnani integration status */

/** The one honest status line shown wherever Gnani is named. Change it when the live run happens. */
export const GNANI_STATUS = {
  integrated: true,
  liveRun: false,
  short: "Integrated. Live numbers coming.",
  long: "The Gnani code paths are written and tested against fakes. They have not yet been run against the live API because our key has not arrived, so this site publishes no recognition or voice number yet.",
} as const;

export interface GnaniModel {
  id: "prisma" | "timbre" | "clone";
  name: string;
  /** Exact API names, as used in docs/GNANI.md. */
  api: string[];
  role: string;
  /** Where it lives in the repository. */
  code: string;
}

export const GNANI_MODELS: GnaniModel[] = [
  {
    id: "prisma",
    name: "Prisma v3",
    api: ["POST /stt/v3", "wss://api.vachana.ai/stt/v3/stream"],
    role: "Hears the caller. REST per utterance today; the realtime WebSocket at 8 kHz is being wired onto the phone path. format=codemix keeps English words in Latin and numbers spelled in the caller's language. keyterms carry the business's staff and service names from its own documents; previous_text carries the caller's last sentence.",
    code: "backend/services/gnani_client.py, gnani.py",
  },
  {
    id: "timbre",
    name: "Timbre",
    api: ["POST /api/v1/tts/inference"],
    role: "Speaks for the front desk. Raw 8 kHz audio goes straight to the phone line, so there is no extra resampling. Reads dates, amounts and phone numbers the way an Indian caller expects.",
    code: "backend/services/gnani.py, routers/phone.py",
  },
  {
    id: "clone",
    name: "Voice cloning",
    api: ["POST /api/v1/tts/voice-clone/embeddings", "model: vachana-vc-v1"],
    role: "An owner can give the agent a consented voice. The recording becomes a speaker embedding, and Timbre speaks with it. The consent tick, the label and the receipt stay in front of it.",
    code: "backend/services/gnani_client.py (clone_embedding), voice_vault.py",
  },
];

/** What reasons, and what happens when Gnani cannot answer. */
export const REASONING = {
  llm: "Embeddings Flash (Google, hosted) writes the replies and chooses the tools. Embeddings embeddings search the business's documents, with keyword search as the backup. Gnani does the speech; Embeddings does the thinking.",
  whenBusy: "There is no second speech model. When Gnani is busy or down, the caller hears a short Timbre line rendered ahead of time and is offered a callback.",
} as const;

/* ------------------------------------------------------------------ languages */

export const LANGUAGES = ["Hindi", "Marathi", "English", "Code-mixed"] as const;

/* ------------------------------------------------------------------ the report card (empty until Prisma is run) */

export const REPORT = {
  /** True once reportCardResults.json holds a run. */
  measured: STRESS_RUN !== null,
  provider: "Gnani Prisma",
  conditions: CONDITIONS.length,
  /** Invented booking calls per condition. */
  clips: 24,
} as const;

/* ------------------------------------------------------------------ other measured numbers (docs/RESULTS.md) */

/** The only thing measured so far: Caller Theatre's scripted run. It shows the path and the rules work, not what a model does. */
export const THEATRE_RUN = {
  passed: 9,
  of: 9,
  label: "Scripted model, text run (audio run pending), latency not meaningful",
} as const;

/* ------------------------------------------------------------------ honest limitations */

export const LIMITATIONS: { title: string; body: string }[] = [
  {
    title: "Gnani models not yet run live",
    body: "Prisma, Timbre and cloning are integrated and tested against fakes. The first live run needs our API key. Until then the report card is empty and says so.",
  },
  {
    title: "Synthetic audio, simulated noise",
    body: "The report card will use invented bookings spoken by synthetic voices, with noise and codecs added by software. It will show where the cliff is, not what a real caller will score.",
  },
  {
    title: "Digits are the hard part",
    body: "Whether Prisma hears a ten-digit phone number whole is not measured yet. That is why the agent reads every number back and commits only on a spoken yes.",
  },
  {
    title: "Missed-call callback not tried on a live number",
    body: "The callback is built and tested with fakes: a Vobiz number whose Answer URL is a signed link is turned away unanswered and the front desk rings back. Vobiz itself, and a missed call on a real Vobiz number, have not been tried.",
  },
  {
    title: "Calendar and inbound calls untested live",
    body: "Google Calendar sync and calls received on your own number are written and tested against fakes, not against a real calendar or line. The bookings list itself is real: it reads the appointments store.",
  },
  {
    title: "Caller Theatre ran on a scripted model",
    body: "All nine scenarios pass with a scripted stand-in for the language model. That shows the tools, the schedule rules and the read-back work end to end; it says nothing about what Embeddings Flash does with the same calls, and its latency is not meaningful because the stand-in answers instantly.",
  },
  {
    title: "Hours from words are rules, not magic",
    body: "The plain-language hours reader handles English, Hindi and Marathi phrases it knows, shows what it understood for review and lists what it did not. A guess is flagged, and it never saves by itself.",
  },
  {
    title: "Reasoning is not Gnani's",
    body: "Embeddings Flash, hosted by Google, writes the replies. Document text and the caller's words reach Google when it does. When Gnani is down there is no second speech model: the caller hears a short held line and is offered a callback.",
  },
  {
    title: "Not a legal product",
    body: "Consent, labels and deletion are built to match the direction of India's rules on synthetic media and personal data. That is a design choice, not legal advice.",
  },
];

/* ------------------------------------------------------------------ team */

export const TEAM: { name: string; role: string; tbc?: boolean }[] = [
  { name: "Ayush Chougula", role: "Builds the phone pipeline, the schedules and this site" },
  { name: "Teammate (TBC)", role: "Role to be confirmed", tbc: true },
];

/* ------------------------------------------------------------------ the example call on the landing page */

export interface CallLine {
  who: "agent" | "caller";
  /** The line in the caller's language (Marathi, with English words as callers say them). */
  text: string;
  /** English gloss under it. */
  gloss: string;
  /** A small tag shown beside a line, for the moment that matters. */
  tag?: string;
}

/** An invented call. The clinic, the caller and the number are made up; the number is the usual 98765 43210 example.
 *  A digit is mis-heard (0 as 9), the read-back puts it in front of the caller, the caller corrects it. */
export const EXAMPLE_CALL: CallLine[] = [
  { who: "agent", text: "नमस्कार, मी सनराइज क्लिनिकची AI असिस्टंट आहे. सांगा, काय मदत करू?", gloss: "Hello, I am Sunrise Clinic's AI assistant. How can I help?" },
  { who: "caller", text: "गुरुवारी संध्याकाळी डॉक्टरांची appointment हवी आहे.", gloss: "I need a doctor's appointment on Thursday evening." },
  { who: "agent", text: "गुरुवार, ५ नोव्हेंबरला संध्याकाळी ५:३० वाजता वेळ मोकळी आहे. तुमचा फोन नंबर सांगा.", gloss: "Thursday 5 November at 5:30 pm is free. Please tell me your phone number." },
  { who: "caller", text: "नऊ आठ सात सहा पाच, चार तीन दोन एक शून्य.", gloss: "Nine eight seven six five, four three two one zero." },
  { who: "agent", text: "मी परत सांगते: गुरुवार, ५ नोव्हेंबर, संध्याकाळी ५:३०, फोन नंबर ९८७६५ ४३२१९. बरोबर आहे का?", gloss: "Let me read it back: Thursday 5 November, 5:30 pm, phone 98765 43219. Is that right?", tag: "Read-back" },
  { who: "caller", text: "नाही, शेवटचा अंक नऊ नाही, शून्य आहे.", gloss: "No, the last digit is not nine, it is zero.", tag: "Caller corrects one digit" },
  { who: "agent", text: "माफ करा. फोन नंबर ९८७६५ ४३२१०. आता बरोबर आहे का?", gloss: "Sorry. Phone 98765 43210. Is that right now?" },
  { who: "caller", text: "हो, बरोबर.", gloss: "Yes, correct.", tag: "Spoken yes" },
  { who: "agent", text: "बुक केलं आहे: गुरुवार, ५ नोव्हेंबर, संध्याकाळी ५:३०. तुमची सही केलेली पावती तयार आहे.", gloss: "Booked: Thursday 5 November, 5:30 pm. Your signed receipt is ready.", tag: "Committed" },
];
