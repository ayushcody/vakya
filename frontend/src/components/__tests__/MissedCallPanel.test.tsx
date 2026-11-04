import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { MissedCallPanel } from "@/components/agents/MissedCallPanel";
import { PhoneTab } from "@/components/agents/PhoneTab";
import { groupMissedCalls, type MissedCallEvent } from "@/lib/missedCalls";
import { api } from "@/lib/api";
import type { Agent } from "@/types/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

const get = api.get as jest.Mock;
const post = api.post as jest.Mock;
const at = "2026-11-04T05:00:00Z";
const ev = (id: string, hit: string, status: MissedCallEvent["status"], over: Partial<MissedCallEvent> = {}): MissedCallEvent => ({
  id, hit, at, status, reason: null, number_masked: "+91•••••00001", ...over,
});
// newest first, as the server sends them
const events: MissedCallEvent[] = [
  ev("4", "h2", "skipped", { reason: "recent_callback" }),
  ev("3", "h2", "received"),
  ev("2", "h1", "called_back"),
  ev("1", "h1", "received"),
];
const URL = "https://abc.ngrok.app/api/phone/missed-call/TOKEN";
const wrap = (ui: React.ReactElement) => <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>;

function serve(over: { enabled?: boolean; list?: MissedCallEvent[] } = {}) {
  get.mockReset().mockImplementation(async (p: string) => {
    if (p.startsWith("/api/phone/missed-calls")) return { enabled: over.enabled ?? true, delay_s: 8, dedupe_s: 600, events: over.list ?? events };
    if (p.startsWith("/api/phone/status")) return { configured: true };
    throw new Error(`unexpected GET ${p}`);
  });
  post.mockReset().mockResolvedValue({ url: URL, method: "GET", valid_days: 365, enabled: over.enabled ?? true, delay_s: 8, dedupe_s: 600, setup: "" });
}

test("groups events into one line per missed call, newest first, at most ten", () => {
  const lines = groupMissedCalls(events);
  expect(lines.map((l) => [l.hit, l.status])).toEqual([["h2", "skipped"], ["h1", "called_back"]]);
  expect(lines[0].text).toMatch(/already called back/);
  const many = Array.from({ length: 14 }, (_, i) => ev(`e${i}`, `hit${i}`, "called_back"));
  expect(groupMissedCalls(many)).toHaveLength(10);
});

test("shows the link to paste into Vobiz, the on status and the last missed calls", async () => {
  serve();
  render(wrap(<MissedCallPanel agentId="a1" />));
  expect(await screen.findByLabelText("Missed-call link")).toHaveValue(URL);
  expect(post).toHaveBeenCalledWith("/api/phone/missed-call-link", { agent_id: "a1" });
  expect(await screen.findByText("Callbacks on")).toBeInTheDocument();
  expect(await screen.findByText("Called back")).toBeInTheDocument();
  expect(screen.getByText(/already called back a few minutes ago/)).toBeInTheDocument();
  expect(screen.getByText(/XML Application/)).toBeInTheDocument();
});

test("says callbacks are off, and how to turn them on", async () => {
  serve({ enabled: false });
  render(wrap(<MissedCallPanel agentId="a1" />));
  expect(await screen.findByText("Callbacks off")).toBeInTheDocument();
  expect(screen.getByText(/MISSED_CALL_CALLBACK=on/)).toBeInTheDocument();
});

test("explains when there is nothing yet, and when the link cannot be made", async () => {
  serve({ list: [] });
  post.mockRejectedValue(new Error("Set PUBLIC_BASE_URL in backend/.env."));
  render(wrap(<MissedCallPanel agentId="a1" />));
  expect(await screen.findByText("No missed calls yet.")).toBeInTheDocument();
  expect(await screen.findByText(/Set PUBLIC_BASE_URL/)).toBeInTheDocument();
});

test("copies the link", async () => {
  serve();
  const writeText = jest.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  render(wrap(<MissedCallPanel agentId="a1" />));
  fireEvent.click(await screen.findByRole("button", { name: "Copy missed-call link" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(URL));
});

test("is part of the Phone number tab", async () => {
  serve();
  const agent = { id: "a1", name: "Reception" } as unknown as Agent;
  render(wrap(<PhoneTab agent={agent} />));
  expect(await screen.findByRole("heading", { name: /Call back missed calls/ })).toBeInTheDocument();
});
