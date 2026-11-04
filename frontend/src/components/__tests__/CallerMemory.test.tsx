import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import CallersPage from "@/app/(app)/desk/callers/page";
import { orderBookings } from "@/app/(app)/desk/calls/CallerCard";
import { CallerEditor, WhatVakyaRemembers, factKey } from "@/components/memory";
import type { CallerProfile } from "@/components/memory";
import { api } from "@/lib/api";
import { toast } from "sonner";

let search = "";
jest.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), put: jest.fn(), del: jest.fn() }, ApiError: class ApiError extends Error {} }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const get = api.get as jest.Mock;
const put = api.put as jest.Mock;
const del = api.del as jest.Mock;
const now = new Date().toISOString();
const soon = new Date(Date.now() + 3 * 86_400_000).toISOString();
const later = new Date(Date.now() + 9 * 86_400_000).toISOString();
const earlier = new Date(Date.now() - 5 * 86_400_000).toISOString();

const sunita: CallerProfile = {
  id: "ref-sunita", caller_ref: "ref-sunita", number_masked: "••••3210", display_name: "Sunita Sharma", preferred_language: "hi",
  last_call_at: now, last_outcome: "booked", last_booking: { date: "2026-11-12", time: "17:00", purpose: "check-up" },
  follow_ups: [{ note: "ask for the old x-ray", at: now }], summary: "booked Thursday for Rohan", call_count: 3,
  facts: [{ key: "preferred_doctor", label: "Preferred doctor", value: "Dr Rao" }, { key: "preferred_schedule", label: "Preferred schedule", value: "evenings" }],
  people: [{ name: "Sunita Sharma" }, { name: "Rohan", relation: "son", age_band: "child" }], last_person: "Rohan", source: "web call",
  calls: [{ conversation_id: "c1", at: now, returning: false }, { conversation_id: "c2", at: now, returning: true }],
  consent_note: "caller details kept to serve them on their next call; deleted after CALLER_PROFILE_RETENTION_DAYS or on request",
  retention_days: 180, expires_at: now,
};
const asha: CallerProfile = { ...sunita, id: "ref-asha", caller_ref: "ref-asha", display_name: "Asha Rao", number_masked: "••••9999", facts: [], people: [], source: "phone", follow_ups: [], calls: [] };

const booking = (id: string, start: string, status: "booked" | "cancelled" = "booked") => ({ id, name: "Rohan", service: "Dr. Meera Kulkarni", reason: "check-up", start, date: start.slice(0, 10), time: "17:00", status });

function serve(callers: CallerProfile[] = [asha, sunita]) {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/callers")) return { enabled: true, callers, total: callers.length, limit: 200, offset: 0 };
    if (path.startsWith("/api/bookings")) return { bookings: [booking("A1", earlier), booking("A3", later), booking("A2", soon), booking("A4", soon, "cancelled")] };
    return {};
  });
}
/** Opens a caller's card in the right-hand pane by choosing its row in the list. */
async function pick(name: RegExp): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("row", { name }));
  return screen.findByTestId("caller-card");
}
const view = () => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><CallersPage /></SWRConfig>);

beforeEach(() => {
  search = "";
  put.mockReset().mockResolvedValue(sunita);
  del.mockReset().mockResolvedValue({ status: "deleted" });
  (toast.success as jest.Mock).mockReset();
  (toast.error as jest.Mock).mockReset();
});

test("the list shows the name, number ending, language, people, last call and bookings of each caller", async () => {
  serve();
  view();
  const row = await screen.findByRole("row", { name: /Sunita Sharma/ });
  expect(within(row).getByText("••••3210")).toBeInTheDocument();
  expect(within(row).getByText("Hindi")).toBeInTheDocument();
  expect(within(within(row).getByTestId("family-chips")).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Sunita", "Rohan · son"]);
  expect(within(screen.getByRole("grid", { name: "Callers" })).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(
    ["Name", "Number ending", "Language", "Family", "Last call", "Bookings"],
  );
  expect(screen.queryByTestId("caller-card")).toBeNull();   // nothing is open until a row is chosen
});

test("choosing a caller opens its card in the pane: details as chips, last booking and notes", async () => {
  serve();
  view();
  const card = await pick(/Sunita Sharma/);
  expect(within(card).getByRole("heading", { name: /Sunita Sharma/ })).toBeInTheDocument();
  expect(within(card).getByText("••••3210")).toBeInTheDocument();
  expect(within(card).getByText("Hindi")).toBeInTheDocument();
  expect(within(card).getByText("Web call")).toBeInTheDocument();
  const chips = within(within(card).getByTestId("fact-chips")).getAllByRole("listitem");
  expect(chips.map((c) => c.textContent)).toEqual(["Preferred doctorDr Rao", "Preferred scheduleevenings"]);
  const people = within(card).getByTestId("people-chips");
  expect(within(people).getByText("Rohan")).toBeInTheDocument();
  expect(people.textContent).toContain("(son, child)");
  expect(people.textContent).toContain("last booked");
  expect(within(card).getByText("ask for the old x-ray")).toBeInTheDocument();
  expect(within(card).getByText(/booked Thursday for Rohan/)).toBeInTheDocument();
  expect(card.textContent).not.toMatch(/9876543210/);
  expect(screen.getByRole("complementary", { name: "Caller details" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByTestId("caller-card")).toBeNull();
});

test("the call history lists the calls newest first", async () => {
  serve([sunita]);
  view();
  const card = await pick(/Sunita Sharma/);
  fireEvent.click(within(card).getByText(/Call history \(2\)/));
  const items = within(within(card).getByTestId("caller-calls")).getAllByRole("listitem");
  expect(items[0].textContent).toContain("Recognised on pickup");
  expect(items[1].textContent).toContain("First call from this number");
  expect(within(items[0]).getByRole("link")).toHaveAttribute("href", "/desk/calls?call=c2");
});

test("a link with ?caller=<ref> opens that caller's card at once and shows its bookings, upcoming first", async () => {
  search = "caller=ref-sunita";
  serve();
  view();
  const card = await screen.findByTestId("caller-card");
  expect(card).toHaveAttribute("data-highlighted", "true");
  expect(within(card).getByRole("heading", { name: /Sunita Sharma/ })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /Sunita Sharma/ })).toHaveAttribute("aria-selected", "true");
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/bookings?caller_ref=ref-sunita"));
  const rows = within(await within(card).findByTestId("caller-bookings")).getAllByRole("listitem");
  expect(rows).toHaveLength(4);
  expect(rows.map((r) => r.textContent?.match(/Cancelled|Booked/)?.[0])).toEqual(["Booked", "Cancelled", "Booked", "Booked"]);
  // the other caller did not ask for bookings
  expect(get.mock.calls.filter(([p]) => String(p).includes("caller_ref="))).toHaveLength(1);
});

test("a ?caller= that is not remembered says so", async () => {
  search = "caller=nobody";
  serve();
  view();
  expect(await screen.findByText(/This caller is not remembered/)).toBeInTheDocument();
  expect(await screen.findByRole("row", { name: /Asha Rao/ })).toBeInTheDocument();
  expect(screen.queryByTestId("caller-card")).toBeNull();
  expect(document.querySelector("[data-highlighted]")).toBeNull();
});

test("the bookings of a card are fetched when the owner opens them", async () => {
  serve([sunita]);
  view();
  const card = await pick(/Sunita Sharma/);
  expect(within(card).queryByTestId("caller-bookings")).toBeNull();   // the list stays closed until it is opened (the family card reads the same bookings for next and last)
  fireEvent.click(within(card).getByRole("button", { name: "Show bookings" }));
  expect(await within(card).findByTestId("caller-bookings")).toBeInTheDocument();
  fireEvent.click(within(card).getByRole("button", { name: "Hide bookings" }));
  expect(within(card).queryByTestId("caller-bookings")).toBeNull();
});

test("orderBookings puts the upcoming ones first, soonest first, then the past ones, latest first", () => {
  const rows = [booking("P1", earlier), booking("U2", later), booking("U1", soon), booking("P2", new Date(Date.now() - 86_400_000).toISOString())];
  expect(orderBookings(rows).map((b) => b.id)).toEqual(["U1", "U2", "P2", "P1"]);
});

test("the owner edits a detail inline, adds a note and saves; the list refreshes", async () => {
  serve([sunita]);
  view();
  const card = await pick(/Sunita Sharma/);
  fireEvent.click(within(card).getByRole("button", { name: "Edit" }));
  const editor = await within(card).findByTestId("caller-editor");
  fireEvent.change(within(editor).getByLabelText("Name"), { target: { value: "Sunita Sharma Jr" } });
  fireEvent.change(within(editor).getByLabelText("Preferred doctor"), { target: { value: "Dr Mehta" } });
  fireEvent.click(within(editor).getByRole("button", { name: "Remove Preferred schedule" }));
  fireEvent.change(within(editor).getByLabelText("New detail name"), { target: { value: "Seating" } });
  fireEvent.change(within(editor).getByLabelText("New detail value"), { target: { value: "window" } });
  fireEvent.click(within(editor).getByRole("button", { name: "Add" }));
  fireEvent.change(within(editor).getByLabelText("Person 2 relation"), { target: { value: "daughter" } });
  fireEvent.click(within(editor).getByRole("button", { name: "Remove note 1" }));
  fireEvent.change(within(editor).getByLabelText("New note for the next call"), { target: { value: "bring the report" } });
  const before = get.mock.calls.length;
  fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  const [path, body] = put.mock.calls[0];
  expect(path).toBe("/api/callers/ref-sunita");
  expect(body).toMatchObject({
    display_name: "Sunita Sharma Jr", preferred_language: "hi",
    facts: { preferred_doctor: "Dr Mehta", preferred_schedule: null, seating: "window" },
    follow_ups: [], add_note: "bring the report",
  });
  expect(body.people).toEqual([{ name: "Sunita Sharma", relation: null, age_band: null }, { name: "Rohan", relation: "daughter", age_band: "child" }]);
  await waitFor(() => expect(toast.success).toHaveBeenCalled());
  await waitFor(() => expect(get.mock.calls.length).toBeGreaterThan(before));
  await waitFor(() => expect(screen.queryByTestId("caller-editor")).toBeNull());
});

test("what the server refuses to keep is shown in the editor and nothing is closed", async () => {
  put.mockRejectedValue(new Error("note: Vakya does not keep identity numbers, card numbers, OTPs, passwords or health details."));
  const onSaved = jest.fn();
  render(<CallerEditor profile={sunita} onSaved={onSaved} onCancel={() => {}} />);
  fireEvent.change(screen.getByLabelText("New note for the next call"), { target: { value: "her Aadhaar is 2345 6789 0123" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/does not keep identity numbers/);
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.getByTestId("caller-editor")).toBeInTheDocument();
});

test("Forget this caller on the Callers page asks, deletes and refreshes", async () => {
  serve([sunita]);
  view();
  const card = await pick(/Sunita Sharma/);
  fireEvent.click(within(card).getByRole("button", { name: "Forget this caller" }));
  serve([]);
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Forget this caller" }));
  await waitFor(() => expect(del).toHaveBeenCalledWith("/api/callers/ref-sunita"));
  await waitFor(() => expect(screen.queryByTestId("caller-card")).toBeNull());
  expect(await screen.findByText("No callers remembered yet")).toBeInTheDocument();
});

test("factKey makes a plain key from the words typed", () => {
  expect(factKey("Preferred doctor")).toBe("preferred_doctor");
  expect(factKey("  Seat / table #2 ")).toBe("seat_table_2");
  expect(factKey("???")).toBe("");
});

test("the What Vakya remembers panel shows this call's details first, then what is kept from earlier calls", () => {
  render(<WhatVakyaRemembers profile={sunita} slots={{ phone: "9876543210", name: "Sunita Sharma", "booking for": "Rohan (son)" }} />);
  const panel = screen.getByTestId("remembers-panel");
  const slots = within(within(panel).getByTestId("call-slots")).getAllByRole("listitem").map((l) => l.textContent);
  expect(slots).toEqual(["nameSunita Sharma", "booking forRohan (son)", "phone••••3210"]);
  expect(within(panel).getByTestId("fact-chips")).toBeInTheDocument();
  expect(within(panel).getByTestId("people-chips")).toBeInTheDocument();
  expect(within(panel).getByText("ask for the old x-ray")).toBeInTheDocument();
  expect(panel.textContent).not.toMatch(/9876543210/);
});

test("the panel says so when nothing is known yet", () => {
  render(<WhatVakyaRemembers profile={null} slots={null} title="Remembered" />);
  expect(screen.getByRole("region", { name: "Remembered" })).toHaveTextContent(/Nothing yet/);
});

// ---------------------------------------------------------------------------------- one number, the whole family

const kulkarni: CallerProfile = {
  ...sunita, id: "ref-rahul", caller_ref: "ref-rahul", display_name: "Rahul Kulkarni", number_masked: "••••4455", facts: [], follow_ups: [],
  people: [
    { name: "Sunita Kulkarni", relation: "mother", age_band: "senior", last_booked_at: now,
      facts: [{ key: "preferred_doctor", label: "Preferred doctor", value: "Dr. Joshi" }], last_booking: { date: "2026-10-12", time: "10:00", purpose: "follow-up" } },
    { name: "Rahul Kulkarni", relation: "self" },
  ],
  last_person: "Sunita Kulkarni",
};
const familyBookings = [
  { id: "F1", name: "Sunita Kulkarni", service: "Dr. Joshi", reason: "follow-up", start: soon, date: soon.slice(0, 10), time: "17:00", status: "booked" },
  { id: "F2", name: "Rahul Kulkarni", service: "Dr. Joshi", reason: "check-up", start: later, date: later.slice(0, 10), time: "09:00", status: "booked" },
];
function serveFamily(next: CallerProfile[] = [kulkarni]) {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/callers")) return { enabled: true, callers: next, total: next.length, limit: 200, offset: 0 };
    if (path.startsWith("/api/bookings")) return { bookings: familyBookings };
    return {};
  });
}

test("the list shows the family as small chips with the relation", async () => {
  serveFamily();
  view();
  const row = await screen.findByRole("row", { name: /Rahul Kulkarni/ });
  expect(within(within(row).getByTestId("family-chips")).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Sunita · mother", "Rahul · self"]);
});

test("the details pane has a family card per person: relation, age band, usual details, last and next booking", async () => {
  serveFamily();
  view();
  await pick(/Rahul Kulkarni/);
  const family = await screen.findByTestId("family-card");
  expect(within(family).getByRole("heading", { name: "One number, the whole family" })).toBeInTheDocument();
  expect(within(family).getByText(/Is this for Sunita again, or for someone else\?/)).toBeInTheDocument();
  const [mother, self] = within(family).getAllByTestId("family-person");
  expect(within(mother).getByText("Sunita Kulkarni")).toBeInTheDocument();
  expect(within(mother).getByText("Mother · 60+")).toBeInTheDocument();
  expect(within(mother).getByText("Dr. Joshi")).toBeInTheDocument();
  expect(within(mother).getByText(/12 Oct, 10 am · follow-up/)).toBeInTheDocument();
  await waitFor(() => expect(within(mother).getByText(/5 pm · follow-up/)).toBeInTheDocument());   // the next booking, from /api/bookings
  expect(within(self).getByText("Self")).toBeInTheDocument();
  await waitFor(() => expect(within(self).getByText(/check-up/)).toBeInTheDocument());
  expect(get.mock.calls.some(([p]) => String(p) === "/api/bookings?caller_ref=ref-rahul")).toBe(true);
});

test("the owner changes a person's relation from the family card", async () => {
  serveFamily();
  put.mockResolvedValue(kulkarni);
  view();
  await pick(/Rahul Kulkarni/);
  const [mother] = await screen.findAllByTestId("family-person");
  fireEvent.click(within(mother).getByRole("button", { name: "Edit Sunita Kulkarni" }));
  fireEvent.change(within(mother).getByLabelText("Name"), { target: { value: "Sunita Joshi" } });
  fireEvent.change(within(mother).getByLabelText("Relation to the caller"), { target: { value: "grandmother" } });
  fireEvent.click(within(mother).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(put).toHaveBeenCalledWith("/api/callers/ref-rahul/people/Sunita%20Kulkarni", { name: "Sunita Joshi", relation: "grandmother", age_band: "senior" }));
  expect(toast.success).toHaveBeenCalled();
});

test("a person the server refuses is shown with its reason and stays editable", async () => {
  serveFamily();
  put.mockRejectedValue(new Error("relation: use mother, father, son"));
  view();
  await pick(/Rahul Kulkarni/);
  const [mother] = await screen.findAllByTestId("family-person");
  fireEvent.click(within(mother).getByRole("button", { name: "Edit Sunita Kulkarni" }));
  fireEvent.click(within(mother).getByRole("button", { name: "Save" }));
  expect(await within(mother).findByRole("alert")).toHaveTextContent("relation: use mother, father, son");
});

test("the owner forgets one person after confirming; the caller stays", async () => {
  serveFamily();
  del.mockResolvedValue({ ...kulkarni, people: [kulkarni.people![1]] });
  view();
  await pick(/Rahul Kulkarni/);
  const [mother] = await screen.findAllByTestId("family-person");
  fireEvent.click(within(mother).getByRole("button", { name: "Forget Sunita Kulkarni" }));
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent("The caller and the rest of the family stay");
  expect(del).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Forget Sunita" }));
  await waitFor(() => expect(del).toHaveBeenCalledWith("/api/callers/ref-rahul/people/Sunita%20Kulkarni"));
  expect(del).not.toHaveBeenCalledWith("/api/callers/ref-rahul");
});

test("a number with nobody booked for yet says so", async () => {
  serveFamily([{ ...kulkarni, people: [] }]);
  view();
  await pick(/Rahul Kulkarni/);
  expect(await screen.findByText(/Nobody yet\. When this number books for someone/)).toBeInTheDocument();
});
