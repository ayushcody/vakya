import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import CallsPage from "@/app/(app)/desk/calls/page";
import {
  addDays, bookingText, callerCell, channelKind, clockLabel, clockWords, dayHeading, dayKey, deskCell, durationLabel, durationShort, maskNumber, parseRange,
  rangeApiQuery, rangeDays, rangeQuery, whenLabel,
} from "@/app/(app)/desk/calls/format";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() }, ApiError: class ApiError extends Error {} }));
jest.mock("@/hooks/useApi", () => ({ useAgents: () => ({ data: [{ id: "a1", name: "Clinic desk" }, { id: "a2", name: "Dental desk" }] }) }));

jest.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(window.location.search) }));   // the address is the test's own

const get = api.get as jest.Mock;
const now = new Date().toISOString();
const base = { agent_id: "a1", agent_name: "Clinic desk", practice: false, when: now, caller: null, duration_s: null, replies: 2, preview: "I want to see a doctor", language: "hi", empty: false, booking: null };
const calls = [
  { ...base, id: "c1", channel: "phone", duration_s: 125, caller_name: "Priya Sharma",
    outcome: { key: "booked", label: "Booked", detail: "Booking A12 · Thu 12 Nov, 7 pm", request_id: null }, booking: { id: "A12", date: "2026-11-12", time: "19:00", with: null } },
  { ...base, id: "c2", channel: "website", caller: "••••3210", agent_id: "a2", agent_name: "Dental desk", preview: "Can I see the dentist",
    outcome: { key: "handed_off", label: "Needs a person", detail: "The caller asked for a person at the desk.", request_id: null } },
  { ...base, id: "c3", channel: "playground", practice: true, outcome: { key: "couldnt_answer", label: "Couldn't answer", detail: "The agent said it did not know: Do you treat dogs?", request_id: null } },
  { ...base, id: "c4", channel: "phone", called_back: true, agent_name: "Deleted agent", outcome: { key: "ended_early", label: "Ended early", detail: null, request_id: null } },
];
const transcript = [
  { role: "caller", text: "I want to see a doctor", at: now, actions: [] },
  { role: "agent", text: "Booked for Saturday.", at: now, actions: [{ name: "book_appointment", ok: true, summary: "booking A12 with Dr. Meera on Saturday 12:30" }] },
];

function serve(list: unknown[], extra: Record<string, unknown> = {}) {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/phone/live")) return { calls: [] };      // nothing live right now
    if (path.startsWith("/api/callers")) return { enabled: false, callers: [], total: 0, limit: 200, offset: 0 };
    if (path.startsWith("/api/usage/calls")) return { days: 30, estimated: true, calls: {} };
    if (path.startsWith("/api/calls/")) return { ...calls[0], transcript };
    return { calls: list, days: 7, timezone: "Asia/Kolkata", empty_hidden: 0, truncated: false, ...extra };
  });
}
const view = () => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><CallsPage /></SWRConfig>);
const rows = () => screen.getAllByRole("row").filter((r) => r.hasAttribute("data-row-id"));

beforeEach(() => { window.history.replaceState(null, "", "/desk/calls"); try { localStorage.clear(); } catch { /* none */ } });

test("lists calls as rows with caller, front desk, channel, outcome, booking and duration", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  expect(rows()).toHaveLength(4);
  const first = within(rows().find((r) => r.dataset.rowId === "c1")!);
  expect(first.getByText("Clinic desk")).toBeInTheDocument();
  expect(first.getByText("Phone")).toBeInTheDocument();
  expect(first.getByText("Booked")).toBeInTheDocument();
  expect(first.getByText("A12 · Thu 12 Nov, 7 pm")).toBeInTheDocument();
  expect(first.getByText("2:05")).toBeInTheDocument();
  expect(first.getByRole("link", { name: /Open booking A12/ })).toHaveAttribute("href", "/desk/bookings?booking=A12");
  const web = within(rows().find((r) => r.dataset.rowId === "c2")!);
  expect(web.getByText("••••3210")).toBeInTheDocument();
  expect(web.getByText("Browser")).toBeInTheDocument();
  expect(web.getByText("Needs a person")).toBeInTheDocument();
  expect(within(rows().find((r) => r.dataset.rowId === "c3")!).getByText("Practice")).toBeInTheDocument();
});

test("a deleted front desk and a missing number read as muted words, a callback has its own channel", async () => {
  serve(calls);
  view();
  const row = within((await screen.findAllByRole("row")).find((r) => r.dataset.rowId === "c4")!);
  expect(row.getByText("Front desk deleted")).toBeInTheDocument();
  expect(row.getByText("No number")).toBeInTheDocument();
  expect(row.getByText("Missed-call callback")).toBeInTheDocument();
  expect(screen.queryByText(/Deleted agent|Number not recorded/)).toBeNull();
});

test("asks the server for the last 7 days without empty calls by default, and groups rows under Today", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  expect(get).toHaveBeenCalledWith("/api/calls?range=7d&limit=200&hide_empty=true");
  expect(screen.getByRole("rowheader", { name: "Today" })).toBeInTheDocument();
});

test("the time range selector asks for that range and keeps it in the address", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  fireEvent.click(screen.getByRole("button", { name: "Yesterday" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/calls?range=yesterday&limit=200&hide_empty=true"));
  expect(window.location.search).toBe("?range=yesterday");
  fireEvent.click(screen.getByRole("button", { name: /Custom/ }));
  const from = screen.getByLabelText("From") as HTMLInputElement;
  fireEvent.change(from, { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("to"), { target: { value: "2026-10-05" } });
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/calls?from=2026-10-01&to=2026-10-05&limit=200&hide_empty=true"));
  expect(window.location.search).toBe("?from=2026-10-01&to=2026-10-05");
  fireEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
  expect(window.location.search).toBe("");
});

test("a link with ?range= or ?from=&to= reproduces the view", async () => {
  window.history.replaceState(null, "", "/desk/calls?range=30d");
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  expect(get).toHaveBeenCalledWith("/api/calls?range=30d&limit=200&hide_empty=true");
  expect(screen.getByRole("button", { name: "Last 30 days" })).toHaveAttribute("aria-pressed", "true");
});

test("Hide empty calls is on by default and turning it off asks for them", async () => {
  serve(calls, { empty_hidden: 5 });
  view();
  await screen.findByText("Priya Sharma");
  expect(screen.getByText(/5 empty calls hidden/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Hide empty calls" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/calls?range=7d&limit=200"));
});

test("columns sort by a click, with aria-sort, and keep the sort direction", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  const caller = screen.getByRole("columnheader", { name: /Caller/ });
  expect(screen.getByRole("columnheader", { name: /Time/ })).toHaveAttribute("aria-sort", "descending");
  fireEvent.click(within(caller).getByRole("button"));
  expect(caller).toHaveAttribute("aria-sort", "ascending");
  // "••••3210" comes before "Priya Sharma"; the rows without a name or number ("No number", "You") follow in word order
  expect(rows()[0].dataset.rowId).toBe("c2");
  fireEvent.click(within(caller).getByRole("button"));
  expect(caller).toHaveAttribute("aria-sort", "descending");
  expect(rows()[0].dataset.rowId).toBe("c3");   // "You" sorts after "Priya Sharma" and the numbers
});

test("the outcome buttons count and filter, and the search box finds a number's last digits or a booking id", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  const chips = screen.getByRole("group", { name: "Filter by what happened" });
  expect(within(chips).getByRole("button", { name: /All 4/ })).toBeInTheDocument();
  fireEvent.click(within(chips).getByRole("button", { name: /Needs a person 1/ }));
  expect(rows()).toHaveLength(1);
  fireEvent.click(within(chips).getByRole("button", { name: /All/ }));
  fireEvent.change(screen.getByRole("searchbox", { name: "Search calls" }), { target: { value: "3210" } });
  fireEvent.change(screen.getByRole("searchbox", { name: "Search calls" }), { target: { value: "a12" } });
  expect(rows().map((r) => r.dataset.rowId)).toEqual(["c1"]);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search calls" }), { target: { value: "zzz" } });
  expect(await screen.findByText("Nothing matches these filters")).toBeInTheDocument();
});

test("the Where filter uses the list's channels", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  fireEvent.change(screen.getByLabelText("Where"), { target: { value: "callback" } });
  expect(rows().map((r) => r.dataset.rowId)).toEqual(["c4"]);
});

test("picking a front desk asks the server for that one only", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  fireEvent.change(screen.getByLabelText("Front desk"), { target: { value: "a2" } });
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/calls?range=7d&limit=200&hide_empty=true&agent_id=a2"));
});

test("up and down move the highlight, Enter opens the details pane with the transcript and Escape closes it", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  const first = rows()[0];
  first.focus();
  fireEvent.keyDown(first, { key: "ArrowDown" });
  expect(rows()[1]).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("complementary", { name: "Call details" })).toBeNull();
  fireEvent.keyDown(rows()[1], { key: "ArrowUp" });
  fireEvent.keyDown(rows()[0], { key: "Enter" });
  const pane = await screen.findByRole("complementary", { name: "Call details" });
  expect(await within(pane).findByText("Booked for Saturday.")).toBeInTheDocument();
  expect(within(pane).getByText("booking A12 with Dr. Meera on Saturday 12:30")).toBeInTheDocument();
  expect(get).toHaveBeenCalledWith("/api/calls/c1");
  expect(window.location.search).toBe("?call=c1");
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("complementary", { name: "Call details" })).toBeNull());
  expect(window.location.search).toBe("");
});

test("clicking a row opens its details, and a link inside the row does not", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  fireEvent.click(screen.getByRole("link", { name: /Open booking A12/ }));
  expect(screen.queryByRole("complementary", { name: "Call details" })).toBeNull();
  fireEvent.click(rows()[1]);
  expect(await screen.findByRole("complementary", { name: "Call details" })).toBeInTheDocument();
});

test("a link with ?call= opens that conversation", async () => {
  window.history.replaceState(null, "", "/desk/calls?call=c1");
  serve(calls);
  view();
  expect(await screen.findByRole("complementary", { name: "Call details" })).toBeInTheDocument();
  expect(await screen.findByText("Booked for Saturday.")).toBeInTheDocument();
});

test("a conversation never links to Approvals: bookings are committed on a spoken yes", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  fireEvent.click(rows()[0]);
  await screen.findByText("Booked for Saturday.");
  expect(screen.queryByRole("link", { name: /Approvals/ })).toBeNull();
});

test("the Columns menu hides a column and the density switch is remembered", async () => {
  serve(calls);
  view();
  await screen.findByText("Priya Sharma");
  expect(screen.getByRole("columnheader", { name: /Language/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Columns" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Language" }));
  expect(screen.queryByRole("columnheader", { name: /Language/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Compact rows" }));
  expect(screen.getByRole("button", { name: "Compact rows" })).toHaveAttribute("aria-pressed", "true");
  expect(JSON.parse(localStorage.getItem("listview:calls") ?? "{}")).toEqual({ density: "compact", hidden: ["language"] });
});

test("empty state says what will appear here and offers the Try page and the receptionist", async () => {
  serve([]);
  view();
  expect(await screen.findByText("No conversations yet")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Try a call" })).toHaveAttribute("href", "/desk/try");
  expect(screen.getByRole("link", { name: "Set up a front desk" })).toHaveAttribute("href", "/desk/receptionist");
});

test("an empty Today offers the last 7 days", async () => {
  window.history.replaceState(null, "", "/desk/calls?range=today");
  serve([]);
  view();
  expect(await screen.findByText("No calls today yet")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show the last 7 days" }));
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/calls?range=7d&limit=200&hide_empty=true"));
});

test("a failed load says so and can retry", async () => {
  let failed = false;
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/phone/live")) return { calls: [] };
    if (path.startsWith("/api/callers")) return { enabled: false, callers: [], total: 0, limit: 200, offset: 0 };
    if (path.startsWith("/api/usage")) return { days: 30, estimated: true, calls: {} };
    if (!failed) { failed = true; throw new Error("offline"); }
    return { calls: [], days: 7 };
  });
  view();
  expect(await screen.findByText("Could not load the conversations")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Try again|Retry/i }));
  expect(await screen.findByText("No conversations yet")).toBeInTheDocument();
});

test("format helpers", () => {
  expect(maskNumber("+91 98765 43210")).toBe("••••3210");
  expect(maskNumber("")).toBe("Number not given");
  expect(durationLabel(125)).toBe("2 min 05 s");
  expect(durationShort(125)).toBe("2:05");
  expect(durationShort(45)).toBe("45 s");
  expect(durationShort(null)).toBeNull();
  const noon = new Date("2026-10-17T12:00:00");
  expect(whenLabel("2026-10-17T09:30:00", noon)).toMatch(/^Today, /);
  expect(whenLabel(null)).toBe("Unknown time");
});

test("booking text, days and clock times are in words in the desk's time zone", () => {
  expect(bookingText({ id: "a56", date: "2026-11-12", time: "19:00" })).toBe("A56 · Thu 12 Nov, 7 pm");
  expect(bookingText({ id: "A5", date: "2026-11-02", time: "17:30" })).toBe("A5 · Mon 2 Nov, 5:30 pm");
  expect(bookingText({ id: "A5", date: null, time: "00:05" })).toBe("A5 · 12:05 am");
  expect(bookingText({ id: "A5" })).toBe("A5");
  expect(bookingText(null)).toBeNull();
  expect(clockWords("12:00")).toBe("12 pm");
  expect(clockWords("25:00")).toBeNull();
  expect(dayKey("2026-11-03T20:30:00Z", "Asia/Kolkata")).toBe("2026-11-04");     // 2 am the next day in India
  expect(dayKey("2026-11-03T20:30:00Z", "UTC")).toBe("2026-11-03");
  expect(clockLabel("2026-11-03T20:30:00Z", "Asia/Kolkata")).toBe("2 am");
  expect(clockLabel("2026-11-03T13:12:00Z", "Asia/Kolkata")).toBe("6:42 pm");
  expect(addDays("2026-11-01", -1)).toBe("2026-10-31");
  expect(dayHeading("2026-11-04", "2026-11-04")).toBe("Today");
  expect(dayHeading("2026-11-03", "2026-11-04")).toBe("Yesterday");
  expect(dayHeading("2026-11-02", "2026-11-04")).toBe("Mon 2 Nov");
  expect(callerCell({ channel: "phone", caller: null }).text).toBe("No number");
  expect(callerCell({ channel: "phone", caller: "••••1", caller_name: "Asha" }, null, null).text).toBe("Asha");
  expect(callerCell({ channel: "website", caller: null }).text).toBe("Unknown");
  expect(callerCell({ channel: "phone", caller: null }, "Ravi", null).text).toBe("Ravi");
  expect(deskCell("Deleted agent")).toEqual({ text: "Front desk deleted", muted: true });
  expect(channelKind({ channel: "phone", called_back: true })).toBe("callback");
});

test("the range in the address", () => {
  const q = (s: string) => { const p = new URLSearchParams(s); return parseRange((n) => p.get(n)); };
  expect(q("")).toEqual({ kind: "preset", key: "7d" });
  expect(q("range=today")).toEqual({ kind: "preset", key: "today" });
  expect(q("range=nonsense")).toEqual({ kind: "preset", key: "7d" });
  expect(q("from=2026-11-01&to=2026-11-03")).toEqual({ kind: "custom", from: "2026-11-01", to: "2026-11-03" });
  expect(q("from=soon")).toEqual({ kind: "preset", key: "7d" });
  expect(rangeQuery({ kind: "preset", key: "7d" })).toBe("");
  expect(rangeQuery({ kind: "preset", key: "30d" })).toBe("range=30d");
  expect(rangeApiQuery({ kind: "custom", from: "2026-11-01", to: "" })).toBe("from=2026-11-01");
  expect(rangeDays({ kind: "preset", key: "yesterday" }, "2026-11-04")).toEqual({ from: "2026-11-03", to: "2026-11-03" });
  expect(rangeDays({ kind: "preset", key: "7d" }, "2026-11-04")).toEqual({ from: "2026-10-29", to: "2026-11-04" });
});
