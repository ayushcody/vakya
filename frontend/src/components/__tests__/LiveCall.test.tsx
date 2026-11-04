import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { LiveCall } from "@/components/live/LiveCall";
import { scrollSlotIntoView } from "@/components/live/LiveCalendar";
import { groupRuns, Legend, LiveTranscript } from "@/components/live/LiveTranscript";
import { api } from "@/lib/api";
import { initialLive, reduceLive, type LiveEvent } from "@/lib/liveCall";
import { addDays, fromMin, nowIn, toMin, weekFor, type Schedule } from "@/lib/schedules";

jest.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/desk/try",
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn(), blob: jest.fn(), sse: jest.fn() },
  ApiError: jest.requireActual("@/lib/apiBase").ApiError,
  API_URL: "http://api.test",
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("@/components/schedules/useMediaQuery", () => ({ useMediaQuery: jest.fn() }));
import { useMediaQuery } from "@/components/schedules/useMediaQuery";

const get = api.get as jest.Mock;
const wide = useMediaQuery as jest.Mock;

const schedule: Schedule = {
  id: "S1", agent_id: "a1", name: "Doctor consultations", description: null, kind: "slots", accent: "violet", timezone: "Asia/Kolkata",
  slot_minutes: 30, buffer_minutes: 0, capacity: 1,
  week: weekFor(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], [{ start: "10:00", end: "12:00" }, { start: "17:00", end: "19:00" }]),
  check_in: null, check_out: null, min_notice_minutes: 0, horizon_days: 60, closed_dates: [],
};
const DAY = addDays(nowIn("Asia/Kolkata", Date.now()).date, 2);

function slotsFor(path: string, booked: string | null) {
  const q = new URL(path, "http://x").searchParams;
  const days: unknown[] = [];
  for (let d = q.get("from")!, i = 0; d <= q.get("to")!; d = addDays(d, 1), i++) {
    const starts = [...Array.from({ length: 4 }, (_, k) => toMin("10:00") + 30 * k), ...Array.from({ length: 4 }, (_, k) => toMin("17:00") + 30 * k)];
    days.push({
      date: d, open: true,
      slots: starts.map((m) => {
        const t = fromMin(m);
        const mine = d === DAY && booked === t;
        return { start: `${d}T${t}:00+05:30`, end: `${d}T${fromMin(m + 30)}:00+05:30`, status: mine ? "full" : "free", remaining: mine ? 0 : 1,
          bookings: mine ? [{ id: "A12", name: "Priya Sharma", phone_masked: "••••3210", source: "web" }] : [], block_id: null };
      }),
    });
  }
  return { timezone: "Asia/Kolkata", kind: "slots", days };
}

const env = (seq: number, extra: object) =>
  ({ seq, at: "2026-11-05T10:00:00Z", agent_id: "a1", conversation_id: "c1", turn_id: "t1", ...extra }) as unknown as LiveEvent;
const caller = env(1, { type: "caller", text: "मुझे Thursday शाम 6 बजे चाहिए", typed: false, language: "hi", script: "mixed", unsure: false, words: [["मुझे", "hi"], ["Thursday", "en"], ["शाम", "hi"], ["6", "x"], ["बजे", "hi"], ["चाहिए", "hi"]] });
const tool = (seq: number, phase: "start" | "end", status: string, extra: object = {}) =>
  env(seq, { type: "tool", tool: "check_availability", call_id: "t1:0", phase, status, schedule_id: "S1", schedule: "Doctor consultations", date: DAY, time: "17:30", label: phase === "start" ? `Checking ${DAY} 5:30 pm` : "Checked Thu 5:30 pm: taken, offered 5 pm or 6 pm", ...extra });

let queue: LiveEvent[][] = [];
let booked: string | null = null;
function serve() {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.includes("/events?")) {
      const events = queue.shift() ?? [];
      return { events, last_seq: events.at(-1)?.seq ?? 0, head: 99 };
    }
    if (path.startsWith("/api/schedules/S1/slots")) return slotsFor(path, booked);
    if (path.startsWith("/api/schedules")) return { schedules: [schedule] };
    if (path.includes("/web-call-links")) return { links: [{ id: "awc_1", url: "https://demo.example/call/awc_1", label: "", status: "active" }], base_url: "https://demo.example", public: true, defaults: { expires_in: "7d", max_session_minutes: 10, max_minutes_per_day: 60 } };
    if (path.startsWith("/api/bookings")) return { bookings: [{ id: "A12", name: "Priya Sharma", phone: "••••3210", phone_masked: "••••3210", service: null, specialty: null, reason: null, start: `${DAY}T17:30:00+05:30`, end: `${DAY}T18:00:00+05:30`, timezone: "Asia/Kolkata", status: "booked", conversation_id: "c1", receipt_id: "r1", agent_id: "a1", created_at: new Date().toISOString(), calendar_sync: "not_configured", schedule_id: "S1", schedule_name: "Doctor consultations", source: "web", has_evidence: false }], timezone: "Asia/Kolkata" };
    throw new Error(`unexpected GET ${path}`);
  });
}
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

beforeEach(() => {
  queue = [];
  booked = null;
  wide.mockReturnValue(true);
  serve();
});

describe("the transcript", () => {
  it("underlines each run of one language softly and says honestly when it is not sure", () => {
    const state = reduceLive(initialLive(), [caller, tool(2, "start", "checking"), tool(3, "end", "taken", { available: false, options: [] })]);
    render(<><LiveTranscript lines={state.lines} now={0} /><Legend /></>);
    const runs = Array.from(document.querySelectorAll("[data-lang]"));
    // one run per stretch of one language; the number stays with the Hindi words around it
    expect(runs.map((r) => [r.getAttribute("data-lang"), r.textContent])).toEqual([["hi", "मुझे"], ["en", "Thursday"], ["hi", "शाम 6 बजे चाहिए"]]);
    expect(runs.every((r) => /underline/.test(r.className) && !/\bbg-/.test(r.className))).toBe(true);   // an underline, not a coloured box
    expect(screen.getByText("Hindi · Devanagari and Latin")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("taken, offered 5 pm or 6 pm");
    expect(screen.getByLabelText("What the underlines mean")).toHaveTextContent(/Hindi.*Marathi.*English.*Not sure/);
  });

  it("groups words into runs: letterless words stay with their neighbours, an unsure word has its own dotted run", () => {
    expect(groupRuns([["5", "x"], ["baje", "hi"], ["is", "en"], ["it", "en"], ["free", "en"], ["?", "x"], ["xyz", "x"], ["हाँ", "hi"]])).toEqual([
      { lang: "hi", text: "5 baje" }, { lang: "en", text: "is it free ?" }, { lang: "x", text: "xyz" }, { lang: "hi", text: "हाँ" },
    ]);
    expect(groupRuns([["12", "x"]])).toEqual([{ lang: "x", text: "12" }]);
    expect(groupRuns([])).toEqual([]);
  });

  it("marks the read-back while it is spoken", () => {
    const say = (seq: number, text: string) => env(seq, { type: "agent", index: seq, text, audio_ms: 2000, readback: true, disclosure: false, language: "en", script: "latin", words: [[text, "en"]] });
    const state = reduceLive(initialLive(), [say(1, "Thursday at 5:30 pm."), say(2, "Shall I book it?")], 1000);
    const { rerender } = render(<LiveTranscript lines={state.lines} now={1500} />);
    expect(screen.getByText("Read back")).toBeInTheDocument();
    expect(screen.getByText("speaking")).toBeInTheDocument();
    const active = () => Array.from(document.querySelectorAll('[data-active="true"]')).map((e) => e.textContent);
    expect(active()).toEqual(["Thursday at 5:30 pm."]);
    rerender(<LiveTranscript lines={state.lines} now={3500} />);
    expect(active()).toEqual(["Shall I book it?"]);
    rerender(<LiveTranscript lines={state.lines} now={9000} />);
    expect(screen.queryByText("speaking")).toBeNull();
  });
});

describe("the calendar follows the call", () => {
  function grid(top: number) {
    const root = document.createElement("div");
    const box = document.createElement("div");
    box.style.overflowY = "auto";
    const cell = document.createElement("div");
    cell.setAttribute("data-start", `${DAY}T18:00:00+05:30`);
    box.appendChild(cell);
    root.appendChild(box);
    document.body.appendChild(root);
    Object.defineProperty(box, "scrollHeight", { value: 2000 });
    Object.defineProperty(box, "clientHeight", { value: 400 });
    Object.defineProperty(cell, "clientHeight", { value: 40 });
    box.getBoundingClientRect = () => ({ top: 100 } as DOMRect);
    cell.getBoundingClientRect = () => ({ top } as DOMRect);
    const scrollTo = jest.fn();
    box.scrollTo = scrollTo as unknown as typeof box.scrollTo;
    return { root, box, scrollTo };
  }

  it("scrolls the calendar's own box so the slot is in the middle, and never the page", () => {
    const { root, scrollTo } = grid(1100);        // 1000 px below the top of the box
    expect(scrollSlotIntoView(root, `${DAY}T18:00`, true)).toBe(true);
    expect(scrollTo).toHaveBeenCalledWith({ top: 1000 - 200 + 20, behavior: "smooth" });
    scrollTo.mockClear();
    scrollSlotIntoView(root, `${DAY}T18:00`, false);
    expect(scrollTo).toHaveBeenCalledWith({ top: 820, behavior: "auto" });
    root.remove();
  });

  it("says when the slot is not there yet", () => {
    const { root, scrollTo } = grid(100);
    expect(scrollSlotIntoView(root, `${DAY}T19:30`, true)).toBe(false);
    expect(scrollSlotIntoView(null, `${DAY}T18:00`, true)).toBe(false);
    expect(scrollTo).not.toHaveBeenCalled();
    root.remove();
  });
});

describe("the split screen", () => {
  it("shows the QR above, follows the call and turns the slot booked with a toast", async () => {
    queue = [[caller, tool(2, "start", "checking"), tool(3, "end", "taken", { available: false, options: [{ date: DAY, time: "17:00" }, { date: DAY, time: "18:00" }] })]];
    view(<LiveCall agentId="a1" />);
    expect(await screen.findByText("Scan to call from your phone")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "QR code of the web call link" })).toBeInTheDocument();
    expect(screen.getByText("https://demo.example/call/awc_1")).toBeInTheDocument();
    expect(await screen.findByText("Checked Thu 5:30 pm: taken, offered 5 pm or 6 pm")).toBeInTheDocument();
    expect(screen.getByText("On a call")).toBeInTheDocument();
    const style = await waitFor(() => {
      const s = document.querySelector(".live-cal style")?.textContent ?? "";
      expect(s).toContain(`[data-start^="${DAY}T17:30"]`);
      return s;
    });
    expect(style).toContain('content:"Taken"');
    expect(style.match(/content:"Offered"/g)).toHaveLength(2);
    expect(await screen.findByRole("grid", { name: "Doctor consultations calendar" })).toBeInTheDocument();

    booked = "17:30";
    queue = [[
      env(4, { type: "tool", tool: "book", call_id: "t3:0", phase: "end", status: "booked", schedule_id: "S1", date: DAY, time: "17:30", booking_id: "A12", label: "Booked A12" }),
      env(5, { type: "booking", booking_id: "A12", name: "Priya Sharma", schedule_id: "S1", schedule: "Doctor consultations", date: DAY, time: "17:30", start: `${DAY}T17:30`, evidence_id: "e1", receipt_id: "e1", evidence_complete: true }),
    ]];
    const toast = await screen.findByTestId("booking-toast", {}, { timeout: 4000 });
    expect(toast).toHaveTextContent("Booked A12");
    expect(toast).toHaveTextContent("Priya S.");
    expect(within(toast).getByRole("button", { name: "See the proof" })).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".live-cal style")?.textContent).toContain("liveFill"));
    // the calendar shows who is in the slot
    await waitFor(() => expect(within(screen.getByRole("grid", { name: "Doctor consultations calendar" })).getByText("Priya S.")).toBeInTheDocument());
  }, 15000);

  it("is two tabs on a phone, and the booking toast shows on both", async () => {
    wide.mockReturnValue(false);
    queue = [[env(1, { type: "booking", booking_id: "A12", name: "Priya Sharma", schedule_id: "S1", schedule: "x", date: DAY, time: "17:30", start: "", evidence_id: null, receipt_id: null, evidence_complete: null })]];
    view(<LiveCall agentId="a1" />);
    const calTab = await screen.findByRole("tab", { name: "Calendar" });
    expect(screen.getByRole("tab", { name: "Call" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("grid")).toBeNull();
    expect(await screen.findByTestId("booking-toast")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(calTab);
    });
    expect(await screen.findByRole("grid", { name: "Doctor consultations calendar" })).toBeInTheDocument();
    expect(screen.getByTestId("booking-toast")).toBeInTheDocument();
  });

  it("offers to make a call link when there is none", async () => {
    get.mockImplementation(async (path: string) => {
      if (path.includes("/web-call-links")) return { links: [], base_url: "", public: false, defaults: { expires_in: "7d", max_session_minutes: 10, max_minutes_per_day: 60 } };
      if (path.includes("/events?")) return { events: [], last_seq: 0, head: 0 };
      if (path.startsWith("/api/schedules/S1/slots")) return slotsFor(path, null);
      if (path.startsWith("/api/schedules")) return { schedules: [schedule] };
      return { bookings: [], timezone: "Asia/Kolkata" };
    });
    view(<LiveCall agentId="a1" />);
    expect(await screen.findByRole("button", { name: /Make a call link/ })).toBeInTheDocument();
    expect(screen.getByText("No call in progress")).toBeInTheDocument();
    expect(screen.getByText("Waiting for a call")).toBeInTheDocument();
  });
});
