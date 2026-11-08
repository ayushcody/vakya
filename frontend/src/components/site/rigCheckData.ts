/** Content for /run-it: how to run Vakya on your own machine. Every line here matches `./vakya` (the repository's one
 *  script), backend/.env.example and docs/GNANI.md. Ports are the defaults (VAKYA_API_PORT, VAKYA_WEB_PORT,
 *  PHONE_PUBLIC_PORT change them). Every model is reached over the network, so nothing is downloaded and no accelerator
 *  is needed; only an Apple M4 with 16 GB has been used, and nothing else is claimed. */

export const REPO_CLONE = "https://github.com/ayushcody/vakya.git";

export const COMMANDS: { cmd: string; note: string }[] = [
  { cmd: `git clone ${REPO_CLONE} && cd vakya`, note: "Get the code." },
  { cmd: "./vakya setup", note: "Checks Python 3.10+ and Node 20+, builds backend/.venv, installs the web packages and creates backend/.env." },
  { cmd: "nano backend/.env", note: "Add GNANI_API_KEY and the LLM_ and EMBEDDINGS_ settings, and the Vobiz keys for phone calls (see the table below)." },
  { cmd: "./vakya start", note: "Starts the API on port 8020 and the web app on port 3010 in the background." },
  { cmd: "./vakya doctor", note: "Checks the machine and the running app and prints a fix for every problem." },
];

export const PORTS: { port: string; what: string }[] = [
  { port: "3010", what: "The web app: this site, the desk at /desk and /verify." },
  { port: "8020", what: "The API. Keep it on your machine; it is never put on the internet." },
  { port: "8021", what: "The phone-only public port. A tunnel points Vobiz at it, and it serves only the call stream, the answer document, the hangup callback, the missed-call link and the web-call page." },
];

export interface KeyRow {
  name: string;
  needed: "Required" | "Recommended" | "For phone calls" | "For the calendar";
  gives: string;
  without: string;
}

export const KEYS: KeyRow[] = [
  {
    name: "GNANI_API_KEY",
    needed: "Required",
    gives: "Gnani Prisma v3 to listen, Timbre to speak, and vachana-vc-v1 voice cloning.",
    without: "Vakya cannot hear or speak. There is no second speech model: the desk, schedules and bookings still open, but no call works.",
  },
  {
    name: "LLM_API_KEY, LLM_BASE_URL, LLM_MODEL",
    needed: "Required",
    gives: "A hosted language model, set in .env, writes the front desk's replies and chooses its tools.",
    without: "No replies: the agent answers every message with a fixed line.",
  },
  {
    name: "EMBEDDINGS_API_KEY, EMBEDDINGS_BASE_URL, EMBEDDINGS_MODEL",
    needed: "Recommended",
    gives: "Hosted embeddings search the business's documents.",
    without: "Document search falls back to keyword search.",
  },
  {
    name: "VOBIZ_AUTH_ID, VOBIZ_AUTH_TOKEN, VOBIZ_CALLER_ID",
    needed: "For phone calls",
    gives: "Real phone calls through a Vobiz number. ./vakya start opens a tunnel so Vobiz can reach your computer. For missed-call callbacks, make a Vobiz XML Application whose Answer URL is the link from Front desk, Phone number, and attach a number to it; built and tested with fakes, not yet tried on a live number.",
    without: "Everything else works, including the browser web call. A trial account only calls verified numbers.",
  },
  {
    name: "GOOGLE_CALENDAR_ID, GOOGLE_SERVICE_ACCOUNT_FILE",
    needed: "For the calendar",
    gives: "Bookings sync to a Google Calendar shared with the service account.",
    without: "Bookings are kept in Vakya's own list.",
  },
];

export const CHECKS: { cmd: string; what: string }[] = [
  { cmd: "backend/.venv/bin/python backend/bench/gnani_smoke.py", what: "First contact with Gnani's API with your key: lists voices, runs Prisma on a test clip, renders Timbre in two formats. Prints every reply shape and never prints the key." },
  { cmd: "./vakya stress --conditions all --provider gnani --keyterms", what: "The acoustic-stress report card on Prisma: the same 24 invented booking calls, clean, 8 kHz, noisy and through a phone codec, with and without keyterms. First run 6 Nov 2026, published on /report-card." },
  { cmd: "./vakya test", what: "The backend and frontend test suites." },
];

export const HARDWARE = {
  tested: "Apple M4 with 16 GB, macOS.",
  notTested: "Linux, Windows (a vakya.ps1 script exists) and Macs with 8 GB are untested.",
  withKeys: "Every model is reached over the network (Gnani for speech, a hosted language model for replies), so nothing is downloaded and the laptop only has to run Python and Node.",
  without: "A call needs the internet the whole time, and a laptop that stays awake if the phone line is served from it.",
};
