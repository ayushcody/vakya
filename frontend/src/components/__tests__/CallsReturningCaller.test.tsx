import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import CallsPage from "@/app/(app)/desk/calls/page";
import { bookingLabel, languageLabel } from "@/app/(app)/desk/calls/format";
import { api } from "@/lib/api";
import { toast } from "sonner";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), del: jest.fn() }, ApiError: class ApiError extends Error {} }));
jest.mock("@/hooks/useApi", () => ({ useAgents: () => ({ data: [{ id: "a1", name: "Clinic desk" }] }) }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const get = api.get as jest.Mock;
const del = api.del as jest.Mock;
const now = new Date().toISOString();
const base = { agent_id: "a1", agent_name: "Clinic desk", practice: false, when: now, caller: null, duration_s: 90, replies: 2, preview: "Hello",
  channel: "phone", outcome: { key: "answered", label: "Answered", detail: null, request_id: null } };
const calls = [{ ...base, id: "c-new" }, { ...base, id: "c-first" }, { ...base, id: "c-stranger" }];

const profile = {
  id: "p1", number_masked: "••••3210", display_name: "Priya Sharma", preferred_language: "hi", last_call_at: now, last_outcome: "booked",
  last_booking: { date: "2026-11-10", time: "16:00", purpose: "dental check-up" },
  follow_ups: [{ note: "ask if she brought the old x-ray", at: now }], summary: "booked", call_count: 2,
  calls: [{ conversation_id: "c-first", at: now, returning: false }, { conversation_id: "c-new", at: now, returning: true }],
  consent_note: "caller details kept to serve them on their next call; deleted after CALLER_PROFILE_RETENTION_DAYS or on request",
  retention_days: 180, expires_at: now,
};

function serve(callers: unknown[] = [profile]) {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/phone/live")) return { calls: [] };
    if (path.startsWith("/api/callers")) return { enabled: true, callers, total: callers.length, limit: 200, offset: 0 };
    if (path.startsWith("/api/calls/")) return { ...calls[0], transcript: [] };
    return { calls, days: 30 };
  });
}
const rowsOf = () => screen.getAllByRole("row").filter((r) => r.hasAttribute("data-row-id"));
const view = () => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><CallsPage /></SWRConfig>);

beforeEach(() => { del.mockReset().mockResolvedValue({ status: "deleted" }); (toast.success as jest.Mock).mockReset(); (toast.error as jest.Mock).mockReset(); });

test("only a call the agent already knew the caller on gets the Returning caller badge", async () => {
  serve();
  view();
  expect(await screen.findAllByTestId("returning-caller")).toHaveLength(1);
  const rows = rowsOf();
  expect(within(rows[0]).getByTestId("returning-caller")).toBeInTheDocument();   // c-new
  expect(within(rows[1]).queryByTestId("returning-caller")).toBeNull();          // c-first: the call that made the profile
  expect(within(rows[2]).queryByTestId("returning-caller")).toBeNull();          // another number
  expect(within(rows[0]).getByText("Priya Sharma")).toBeInTheDocument();         // the profile's name is the caller
  expect(get).toHaveBeenCalledWith("/api/callers?limit=200");
});

test("no profiles, or the feature off, means no badge and no card", async () => {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/phone/live")) return { calls: [] };
    if (path.startsWith("/api/callers")) return { enabled: false, callers: [], total: 0, limit: 200, offset: 0 };
    return { calls, days: 30 };
  });
  view();
  await screen.findAllByText("No number");
  expect(screen.queryByTestId("returning-caller")).toBeNull();
  fireEvent.click(rowsOf()[0]);
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/calls/c-new"));
  expect(screen.queryByTestId("caller-card")).toBeNull();
});

test("a failed callers request leaves the page as it was", async () => {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/phone/live")) return { calls: [] };
    if (path.startsWith("/api/callers")) throw new Error("down");
    return { calls, days: 30 };
  });
  view();
  await screen.findAllByText("No number");
  expect(rowsOf().length).toBe(3);
  expect(screen.queryByTestId("returning-caller")).toBeNull();
});

test("opening the call shows the caller's profile card with what is remembered", async () => {
  serve();
  view();
  await screen.findAllByText("No number");
  fireEvent.click(rowsOf()[0]);
  const card = await screen.findByTestId("caller-card");
  expect(within(card).getByText("Priya Sharma")).toBeInTheDocument();
  expect(within(card).getByText("Called 2 times", { exact: false })).toBeInTheDocument();
  expect(within(card).getByText("••••3210")).toBeInTheDocument();
  expect(within(card).getByText("Hindi")).toBeInTheDocument();
  expect(within(card).getByText(/dental check-up/)).toBeInTheDocument();
  expect(within(card).getByText(/Booked an appointment/)).toBeInTheDocument();
  expect(within(card).getByText("ask if she brought the old x-ray")).toBeInTheDocument();
  expect(within(card).getByText(/deleted after 180 days or on request/)).toBeInTheDocument();
  expect(card.textContent).not.toMatch(/9876543210/);
});

test("the first call of a caller shows the card too, a stranger's call does not", async () => {
  serve();
  view();
  await screen.findAllByText("No number");
  fireEvent.click(rowsOf()[1]);
  expect(await screen.findByTestId("caller-card")).toBeInTheDocument();
  fireEvent.click(rowsOf()[2]);
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/calls/c-stranger"));
  expect(screen.queryByTestId("caller-card")).toBeNull();
});

test("Forget this caller asks first, then deletes the profile and refreshes the list", async () => {
  serve();
  view();
  await screen.findAllByText("No number");
  fireEvent.click(rowsOf()[0]);
  const card = await screen.findByTestId("caller-card");
  fireEvent.click(within(card).getByRole("button", { name: "Forget this caller" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/transcripts stay/)).toBeInTheDocument();
  expect(del).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Keep" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(del).not.toHaveBeenCalled();

  serve([]);                                                                      // the next fetch has no profile
  fireEvent.click(within(card).getByRole("button", { name: "Forget this caller" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Forget this caller" }));
  await waitFor(() => expect(del).toHaveBeenCalledWith("/api/callers/p1"));
  await waitFor(() => expect(toast.success).toHaveBeenCalled());
  await waitFor(() => expect(screen.queryByTestId("caller-card")).toBeNull());
  expect(screen.queryByTestId("returning-caller")).toBeNull();
});

test("a failed delete says so and keeps the card", async () => {
  serve();
  del.mockRejectedValue(new Error("no"));
  view();
  await screen.findAllByText("No number");
  fireEvent.click(rowsOf()[0]);
  const card = await screen.findByTestId("caller-card");
  fireEvent.click(within(card).getByRole("button", { name: "Forget this caller" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Forget this caller" }));
  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(screen.getByTestId("caller-card")).toBeInTheDocument();
});

test("labels for languages and bookings", () => {
  expect(languageLabel("hi")).toBe("Hindi");
  expect(languageLabel("MR")).toBe("Marathi");
  expect(languageLabel("xx")).toBe("xx");
  expect(languageLabel(null)).toBeNull();
  expect(bookingLabel({ date: "2026-11-10", time: "16:00" })).toMatch(/10/);
  expect(bookingLabel({ date: "2026-11-10", time: "16:00" })).toBe("Tue 10 Nov, 4 pm");
  expect(bookingLabel({ time: "09:30" })).toBe("9:30 am");
  expect(bookingLabel({})).toBeNull();
  expect(bookingLabel(null)).toBeNull();
});
