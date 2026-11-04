import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Theatre } from "@/components/theatre/Theatre";
import { lineLabel, newestPerKind, toolChip } from "@/components/theatre/format";
import type { TheatreRun, TheatreScenario } from "@/components/theatre/types";

jest.mock("@/lib/apiBase", () => ({ API_URL: "http://api.test" }));

const metrics = {
  fields_correct: 5, fields_total: 5, fields_correct_pct: 100, turns_to_complete: 2, caller_turns: 2,
  read_back_before_commit: true, readback_catch: null, first_reply_ms: 700, total_ms: 3000,
};

function scenario(n: number, pass: boolean, extra: Partial<TheatreScenario> = {}): TheatreScenario {
  return {
    id: `s${n}`, n, title: `Scenario title ${n}`, persona: `Persona ${n}`, template: "clinic", org: "Clinic", language: "hi", language_label: "Hindi",
    tags: [], channel: { codec: "g711", snr_db: n === 3 ? 5 : null }, setup: [], error: null,
    turns: [
      { i: 1, caller: { move: "open", text: "Caller says hello" }, heard: "Caller says hello", latency: { first_answer_ms: 700 },
        agent: { text: "Agent says it is free", tool_calls: [{ name: "check_availability", ok: true, arguments: { date: "2026-11-05" } }] } },
      { i: 2, caller: { move: "yes", text: "Yes" }, heard: "Yes", latency: { first_answer_ms: 900 },
        agent: { text: "Read back", tool_calls: [{ name: "book", ok: false, result: "failed: NOT DONE: first read this back" }] } },
    ],
    verdict: {
      pass, reason: pass ? "Booked it; every check passed." : "Right time failed: booked 15:00, wanted 20:00",
      checks: [
        { id: "time", label: "Right time", ok: pass, must: true, detail: pass ? "booked 20:00" : "booked 15:00, wanted 20:00" },
        { id: "note", label: "Kept in transcript", ok: false, must: false, detail: "stored as said" },
      ],
      metrics: pass ? metrics : { ...metrics, fields_correct: 4 },
    },
    ...extra,
  };
}

const RUN: TheatreRun = {
  run_id: "20261104-013247", created_at: "2026-11-04T01:34:28+05:30", mode: "scripted", input: "text",
  input_label: "text run (audio run pending)", mode_label: "Scripted model: the tools are real",
  label: "Synthetic callers, text run (audio run pending), scored automatically", stack: {},
  scenarios: [scenario(1, true), scenario(2, false), scenario(3, true)],
  summary: { scenarios: 3, passed: 2, failed: 1, fields_correct: 14, fields_total: 15, fields_correct_pct: 93.3, median_first_reply_ms: 700, median_turns_to_complete: 2, median_total_ms: 3000 },
  limits: ["Every caller is synthetic."],
};

function mockFetch(run: TheatreRun | null, ok = true) {
  global.fetch = jest.fn(async (url: string) => {
    if (!ok) return { ok: false, status: 503, json: async () => ({}) } as Response;
    if (String(url).includes("/api/theatre/runs")) return { ok: true, status: 200, json: async () => ({ runs: [] }) } as Response;
    return { ok: true, status: 200, json: async () => ({ run }) } as Response;
  }) as unknown as typeof fetch;
}

describe("Caller Theatre page", () => {
  it("shows the label, the summary strip and a card per scenario, failures as plainly as passes", async () => {
    mockFetch(RUN);
    render(<Theatre />);
    expect(await screen.findByText("Synthetic callers, text run (audio run pending), scored automatically")).toBeInTheDocument();
    expect(screen.getByText("2 of 3")).toBeInTheDocument();
    expect(screen.getByText("93%")).toBeInTheDocument();
    const cards = screen.getByRole("list", { name: "Scenarios" });
    expect(within(cards).getAllByText("Pass")).toHaveLength(2);
    expect(within(cards).getAllByText("Fail")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Open scenario 2.*failed/ })).toBeInTheDocument();
    expect(screen.getByText(/the noise on each card is planned/)).toBeInTheDocument();
  });

  it("does not present the latency of a scripted model as a result", async () => {
    mockFetch(RUN);
    render(<Theatre />);
    expect(await screen.findByText("not measured")).toBeInTheDocument();
    expect(screen.getByText(/scripted model, latency not meaningful/)).toBeInTheDocument();
    expect(screen.queryByText("Median first reply (text)")).toBeNull();
  });

  it("opens a call: bubbles, tool chips and the scorer's verdict with its reasons", async () => {
    mockFetch(RUN);
    render(<Theatre />);
    fireEvent.click(await screen.findByRole("button", { name: /Open scenario 2/ }));
    expect(await screen.findByText("Caller says hello")).toBeInTheDocument();
    expect(screen.getByText("Agent says it is free")).toBeInTheDocument();
    expect(screen.getByText("checked availability")).toBeInTheDocument();
    expect(screen.getByText("read it back before booking")).toBeInTheDocument();   // a refused book is the read-back, not an error
    expect(screen.getByText("What the scorer found")).toBeInTheDocument();
    expect(screen.getByText("booked 15:00, wanted 20:00")).toBeInTheDocument();
    expect(screen.getAllByText("advisory").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Typed, no audio in a text run.").length).toBe(2);
  });

  it("says plainly when the server cannot be reached and when there is no run", async () => {
    mockFetch(null, false);
    const { unmount } = render(<Theatre />);
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
    unmount();
    mockFetch(null);
    render(<Theatre />);
    await waitFor(() => expect(screen.getByText("No run yet.")).toBeInTheDocument());
  });
});

describe("format helpers", () => {
  it("names the line, marking a text run's noise as planned", () => {
    const s = scenario(3, true);
    expect(lineLabel(s, "text")).toBe("8 kHz G.711, 5 dB noise (planned)");
    expect(lineLabel(s, "audio")).toBe("8 kHz G.711, 5 dB noise");
  });
  it("turns a tool call into words", () => {
    expect(toolChip({ name: "schedules.cancel", ok: false, result: "NOT DONE: ask first" }).text).toBe("asked before cancelling");
    expect(toolChip({ name: "book", ok: false, result: "the slot is gone" }).tone).toBe("bad");
    expect(toolChip({ name: "find_booking", ok: true }).text).toBe("looked up bookings by phone");
  });
  it("keeps the newest run of each kind", () => {
    const row = (id: string, mode: "scripted" | "live", input: "audio" | "text") => ({ run_id: id, mode, input, created_at: id, summary: RUN.summary });
    expect(newestPerKind([row("b", "scripted", "text"), row("a", "scripted", "text"), row("c", "scripted", "audio")]).map((r) => r.run_id)).toEqual(["b", "c"]);
  });
});
