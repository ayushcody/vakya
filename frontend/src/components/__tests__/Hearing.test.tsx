import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { CallDetails } from "@/app/(app)/desk/calls/CallRow";
import { HearingSettings } from "@/components/hearing/HearingSettings";
import { initialHearing, reduceHearing } from "@/components/hearing/liveHearing";
import { Misheard } from "@/components/hearing/Misheard";
import { LiveTranscript } from "@/components/live/LiveTranscript";
import { api } from "@/lib/api";
import { initialLive, reduceLive, type LiveEvent } from "@/lib/liveCall";

jest.mock("@/lib/api", () => {
  const { ApiError } = jest.requireActual("@/lib/apiBase");
  return { api: { get: jest.fn(), post: jest.fn(), del: jest.fn() }, ApiError, API_URL: "http://api.test" };
});
jest.mock("@/hooks/useApi", () => ({ useAgents: () => ({ data: [{ id: "a1", name: "Sunrise Clinic", template_id: "clinic" }, { id: "a2", name: "Harbour Hotel", template_id: "hotel" }], isLoading: false }) }));
jest.mock("next/link", () => ({ __esModule: true, default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => <a href={href} {...rest}>{children}</a> }));

const get = api.get as jest.Mock;
const post = api.post as jest.Mock;
const del = api.del as jest.Mock;
const { ApiError } = jest.requireActual("@/lib/apiBase");

const rule = (n: number, extra: object = {}) => ({ id: `H${n}`, heard: `heard ${n}`, meant: `Meant ${n}`, learnt_from: "owner", created_at: "2026-11-04T10:00:00+05:30", last_used_at: null, uses: 0, ...extra });
function summary(rules: unknown[], vocabulary: unknown[] = []) {
  return { agent_id: "a1", rules, vocabulary, rule_limit: 10, rules_used: rules.length, vocabulary_limit: 40, note: "" };
}
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  del.mockReset();
});

describe("Misheard? under a caller line", () => {
  it("opens a form, starts from the words selected in the line and teaches the desk", async () => {
    post.mockResolvedValue({ message: "From now on Vakya listens for 'Dr. Kulkarni' on this desk.", rule: rule(1), replaced: null, vocabulary_added: true, vocabulary_full: false, rules_used: 1 });
    view(<Misheard agentId="a1" conversationId="c12" turnIndex={3} text="I want Dr. Call Karni tomorrow" />);
    const sel = jest.spyOn(window, "getSelection").mockReturnValue({ toString: () => "Dr. Call Karni" } as unknown as Selection);
    fireEvent.pointerDown(screen.getByRole("button", { name: /Misheard/ }));
    fireEvent.click(screen.getByRole("button", { name: /Misheard/ }));
    sel.mockRestore();
    expect(screen.getByLabelText("What Vakya heard")).toHaveValue("Dr. Call Karni");
    fireEvent.change(screen.getByLabelText("What was meant"), { target: { value: "Dr. Kulkarni" } });
    fireEvent.click(screen.getByRole("button", { name: "Teach Vakya" }));
    expect(await screen.findByRole("status")).toHaveTextContent("From now on Vakya listens for 'Dr. Kulkarni' on this desk.");
    expect(post).toHaveBeenCalledWith("/api/hearing/a1/corrections", { heard: "Dr. Call Karni", meant: "Dr. Kulkarni", conversation_id: "c12", turn_index: 3 });
    expect(screen.queryByLabelText("What was meant")).toBeNull();
  });

  it("asks for both fields, and shows the server's reason when it refuses", async () => {
    post.mockRejectedValue(new ApiError("Vakya never keeps a number that looks like a phone, card or identity number.", 422, "ID_NUMBER", {}));
    view(<Misheard agentId="a1" conversationId="c1" text="hello" />);
    fireEvent.click(screen.getByRole("button", { name: /Misheard/ }));
    fireEvent.click(screen.getByRole("button", { name: "Teach Vakya" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Fill in both");
    expect(post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("What Vakya heard"), { target: { value: "phone" } });
    fireEvent.change(screen.getByLabelText("What was meant"), { target: { value: "98765 43210" } });
    fireEvent.click(screen.getByRole("button", { name: "Teach Vakya" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("never keeps a number");
    expect(post.mock.calls[0][1]).not.toHaveProperty("turn_index");     // a live line has no transcript place
  });

  it("at ten corrections offers to replace the oldest unused one", async () => {
    post.mockRejectedValueOnce(new ApiError("This desk already has 10 corrections.", 409, "RULES_FULL", { info: { oldest_unused: { id: "H1", heard: "old", meant: "Old" } } }));
    post.mockResolvedValueOnce({ message: "From now on Vakya listens for 'New' on this desk.", rule: rule(11), replaced: rule(1), vocabulary_added: true, vocabulary_full: false, rules_used: 10 });
    view(<Misheard agentId="a1" conversationId="c1" text="x" />);
    fireEvent.click(screen.getByRole("button", { name: /Misheard/ }));
    fireEvent.change(screen.getByLabelText("What Vakya heard"), { target: { value: "nu" } });
    fireEvent.change(screen.getByLabelText("What was meant"), { target: { value: "New" } });
    fireEvent.click(screen.getByRole("button", { name: "Teach Vakya" }));
    fireEvent.click(await screen.findByRole("button", { name: /Replace the oldest unused one \(old → Old\)/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("listens for 'New'");
    expect(post.mock.calls[1][1]).toMatchObject({ evict_oldest_unused: true });
  });
});

describe("the call's details pane", () => {
  it("puts Misheard? on the caller's lines only", async () => {
    get.mockImplementation(async () => ({
      id: "c9", agent_id: "a1", agent_name: "Sunrise Clinic", channel: "phone", practice: false, when: "2026-11-04T10:00:00Z", caller: null, duration_s: 60,
      outcome: { key: "answered", label: "Answered", detail: null, request_id: null }, replies: 1, preview: "x",
      transcript: [{ role: "agent", text: "Your name?", at: null, actions: [] }, { role: "caller", text: "Dr. Call Karni", at: null, actions: [] }],
    }));
    view(<CallDetails id="c9" ctx={{ tz: "Asia/Kolkata", today: "2026-11-04", known: {}, usage: {}, live: new Set() }} onClose={() => {}} />);
    const list = await screen.findByRole("list", { name: "Transcript" });
    expect(within(list).getAllByRole("button", { name: /Misheard/ })).toHaveLength(1);
    fireEvent.click(within(list).getByRole("button", { name: /Misheard/ }));
    fireEvent.change(screen.getByLabelText("What Vakya heard"), { target: { value: "Call Karni" } });
    fireEvent.change(screen.getByLabelText("What was meant"), { target: { value: "Kulkarni" } });
    post.mockResolvedValue({ message: "ok", rule: rule(1), replaced: null, vocabulary_added: true, vocabulary_full: false, rules_used: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Teach Vakya" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/hearing/a1/corrections", { heard: "Call Karni", meant: "Kulkarni", conversation_id: "c9", turn_index: 1 }));
  });
});

describe("Settings: Hearing", () => {
  function serve(rules: unknown[], vocabulary: unknown[] = []) {
    get.mockImplementation(async (path: string) => {
      if (path === "/api/hearing/a1") return summary(rules, vocabulary);
      if (path === "/api/hearing/a2") return { ...summary([]), agent_id: "a2" };
      throw new Error(`unexpected GET ${path}`);
    });
  }

  it("counts the corrections used, explains Gnani's limit and lists where each came from", async () => {
    serve([rule(1, { learnt_from: { conversation_id: "c12", turn: 2 } }), rule(2), rule(3), rule(4), rule(5), rule(6), rule(7)], [{ term: "Dr. Kulkarni", added_by: "correction", created_at: null }]);
    view(<HearingSettings />);
    expect(await screen.findByTestId("hearing-counter")).toHaveTextContent("7 of 10 corrections used");
    expect(screen.getByText(/Gnani.s Prisma accepts at most 10 corrections/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Corrections used" })).toHaveAttribute("aria-valuenow", "7");
    const first = screen.getAllByRole("listitem")[0];
    expect(within(first).getByRole("link", { name: "Learnt from a call" })).toHaveAttribute("href", "/desk/calls?call=c12");
    expect(within(first).getByText(/heard 1/)).toBeInTheDocument();
    expect(screen.getAllByText(/Added by you/)).toHaveLength(6);
    expect(screen.getByText("Dr. Kulkarni")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Front desk: Sunrise Clinic" })).toBeInTheDocument();     // two front desks: a choice
  });

  it("deletes a correction and adds one by hand", async () => {
    serve([rule(1)]);
    del.mockResolvedValue({});
    post.mockResolvedValue({ message: "From now on Vakya listens for 'OPD' on this desk.", rule: rule(2), replaced: null, vocabulary_added: true, vocabulary_full: false, rules_used: 2 });
    view(<HearingSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete the correction heard 1 to Meant 1" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/hearing/a1/rules/H1"));
    fireEvent.change(screen.getByLabelText("What Vakya hears"), { target: { value: "o p d" } });
    fireEvent.change(screen.getByLabelText("What it should be"), { target: { value: "OPD" } });
    fireEvent.click(screen.getByRole("button", { name: "Add correction" }));
    expect(await screen.findByText("From now on Vakya listens for 'OPD' on this desk.")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/api/hearing/a1/corrections", { heard: "o p d", meant: "OPD" });    // no call: added by you
  });

  it("when all ten are used, the server's refusal offers to replace the oldest unused one", async () => {
    serve(Array.from({ length: 10 }, (_, i) => rule(i + 1)));
    post.mockRejectedValueOnce(new ApiError("This desk already has 10 corrections, the most Gnani's Prisma accepts.", 409, "RULES_FULL", { info: { oldest_unused: { id: "H1", heard: "heard 1", meant: "Meant 1" } } }));
    view(<HearingSettings />);
    expect(await screen.findByTestId("hearing-counter")).toHaveTextContent("10 of 10 corrections used");
    fireEvent.change(screen.getByLabelText("What Vakya hears"), { target: { value: "eleven" } });
    fireEvent.change(screen.getByLabelText("What it should be"), { target: { value: "Eleven" } });
    fireEvent.click(screen.getByRole("button", { name: "Add correction" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already has 10 corrections");
    expect(screen.getByRole("button", { name: "Replace the oldest unused one" })).toBeInTheDocument();
  });

  it("adds and removes vocabulary words", async () => {
    serve([], [{ term: "Shivajinagar", added_by: "owner", created_at: null }]);
    post.mockResolvedValue({});
    del.mockResolvedValue({});
    view(<HearingSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Shivajinagar from the vocabulary" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/hearing/a1/vocabulary?term=Shivajinagar"));
    fireEvent.change(screen.getByLabelText("A word or name callers say"), { target: { value: "Kothrud" } });
    fireEvent.click(screen.getByRole("button", { name: "Add word" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/hearing/a1/vocabulary", { term: "Kothrud" }));
  });

  it("says so when it cannot load", async () => {
    get.mockRejectedValue(new Error("down"));
    view(<HearingSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load what this front desk has learnt");
  });
});

describe("the live view: Listening for", () => {
  const env = (seq: number, extra: object) => ({ seq, at: "2026-11-05T10:00:00Z", agent_id: "a1", conversation_id: "c1", turn_id: "t1", ...extra }) as unknown as LiveEvent;
  const listening = env(1, { type: "hearing", kind: "listening", expecting: "name", kinds: ["name"], names: ["Sunita"], label: "Listening for: a name (Sunita)" });
  const corrected = env(2, { type: "hearing", kind: "corrected", probable: true, label: "Probably heard 'Dr. Call Karni', corrected to 'Dr. Kulkarni' (rule learnt from call #12)" });
  const caller = env(3, { type: "caller", text: "Dr. Kulkarni please", typed: false, language: "en", script: "latin", unsure: false, words: [["Dr.", "en"], ["Kulkarni", "en"], ["please", "en"]] });

  it("reduces hearing events by turn and ignores the rest and repeats", () => {
    let s = reduceHearing(initialHearing(), [listening, caller, corrected]);
    expect(s.byTurn.t1.map((e) => e.kind)).toEqual(["listening", "corrected"]);
    s = reduceHearing(s, [listening]);
    expect(s.byTurn.t1).toHaveLength(2);
  });

  it("shows the chip with the turn's caller line, and a pending one while the line is still on its way", () => {
    const hearing = reduceHearing(initialHearing(), [listening, corrected]);
    const { rerender } = render(<LiveTranscript lines={[]} now={0} hearing={hearing} />);
    expect(screen.getByTestId("hearing-listening")).toHaveTextContent("Listening for: a name (Sunita)");
    const state = reduceLive(initialLive(), [caller]);
    rerender(<LiveTranscript lines={state.lines} now={0} hearing={hearing} agentId="a1" />);
    expect(screen.getAllByTestId("hearing-listening")).toHaveLength(1);       // not repeated at the end
    expect(screen.getByTestId("hearing-corrected")).toHaveTextContent("Probably heard 'Dr. Call Karni', corrected to 'Dr. Kulkarni'");
    expect(screen.getByRole("button", { name: /Misheard/ })).toBeInTheDocument();
  });

  it("offers no Misheard? on a typed line or without a front desk", () => {
    const typed = reduceLive(initialLive(), [env(5, { type: "caller", text: "typed words", typed: true, language: "en", script: "latin", unsure: false, words: [["typed", "en"], ["words", "en"]] })]);
    render(<LiveTranscript lines={typed.lines} now={0} agentId="a1" />);
    expect(screen.queryByRole("button", { name: /Misheard/ })).toBeNull();
    act(() => undefined);
  });
});
