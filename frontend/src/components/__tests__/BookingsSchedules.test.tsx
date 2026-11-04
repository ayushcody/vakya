import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import BookingsPage from "@/app/(app)/desk/bookings/page";
import { ScheduleGlance } from "@/components/desk/ScheduleGlance";
import { ScheduleView } from "@/components/schedules/ScheduleView";
import { api } from "@/lib/api";
import { addDays, fromMin, nowIn, toMin, weekFor, weekStart, type Schedule } from "@/lib/schedules";

let mockSearch = "";
jest.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mockSearch),
  usePathname: () => "/desk/bookings",
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useParams: () => ({ id: "S1" }),
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
  ApiError: jest.requireActual("@/lib/apiBase").ApiError,
  API_URL: "http://api.test",
}));
jest.mock("@/hooks/useApi", () => ({ useAgents: jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

import * as hooks from "@/hooks/useApi";

// jsdom has no PointerEvent; this one keeps the button and pointerType the drag reads.
class FakePointerEvent extends MouseEvent {
  pointerType: string;
  constructor(type: string, init: MouseEventInit & { pointerType?: string } = {}) {
    super(type, init);
    this.pointerType = init.pointerType ?? "mouse";
  }
}
Object.assign(window, { PointerEvent: FakePointerEvent });

const get = api.get as jest.Mock;
const post = api.post as jest.Mock;
const del = api.del as jest.Mock;

const agents = [
  { id: "a1", name: "Sunrise Clinic", template_id: "clinic", greeting: "", system_instructions: "", supported_languages: ["en"], voice_id: null, created_at: "", document_count: 0 },
  { id: "a2", name: "Harbour Hotel", template_id: "hotel", greeting: "", system_instructions: "", supported_languages: ["en"], voice_id: null, created_at: "", document_count: 0 },
];

const clinic: Schedule & { summary: object } = {
  id: "S1", agent_id: "a1", name: "Doctor consultations", description: null, kind: "slots", accent: "violet", timezone: "Asia/Kolkata",
  slot_minutes: 15, buffer_minutes: 0, capacity: 1,
  week: weekFor(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], [{ start: "10:00", end: "11:00" }]),
  check_in: null, check_out: null, min_notice_minutes: 60, horizon_days: 30, closed_dates: [],
  summary: { today_free: 3, today_booked: 1, next_free: null, hours_text: "" },
};
const rooms: Schedule & { summary: object } = {
  ...clinic, id: "S2", agent_id: "a2", name: "Rooms", kind: "nights", capacity: 10, check_in: "12:00", check_out: "11:00",
  week: weekFor(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], [{ start: "00:00", end: "24:00" }]),
  summary: { today_free: 7, today_booked: 3, next_free: null, hours_text: "" },
};

const today = () => nowIn("Asia/Kolkata", Date.now()).date;

/** Every day of the range open 10:00 to 11:00 in 15-minute slots; the second slot of the 3rd day is full, the third is busy. */
function slotsFor(path: string, kind: "slots" | "nights" = "slots") {
  const q = new URL(path, "http://x").searchParams;
  const out: unknown[] = [];
  for (let d = q.get("from")!, i = 0; d <= q.get("to")!; d = addDays(d, 1), i++) {
    if (kind === "nights") {
      out.push({ date: d, open: true, slots: [{ start: `${d}T12:00:00+05:30`, end: `${addDays(d, 1)}T11:00:00+05:30`, status: "free", remaining: 7, bookings: [], block_id: null }] });
      continue;
    }
    const slots = [0, 15, 30, 45].map((m, k) => ({
      start: `${d}T${fromMin(toMin("10:00") + m)}:00+05:30`,
      end: `${d}T${fromMin(toMin("10:00") + m + 15)}:00+05:30`,
      status: i === 2 && k === 1 ? "full" : i === 2 && k === 2 ? "blocked" : "free",
      remaining: i === 2 && (k === 1 || k === 2) ? 0 : 1,
      bookings: i === 2 && k === 1 ? [{ id: "A5", name: "Priya" }] : [],
      block_id: i === 2 && k === 2 ? "K1" : null,
    }));
    out.push({ date: d, open: true, slots });
  }
  return { timezone: "Asia/Kolkata", kind, days: out };
}

function serve(over: Record<string, unknown> = {}) {
  get.mockReset().mockImplementation(async (path: string) => {
    for (const [prefix, body] of Object.entries(over)) if (path.startsWith(prefix)) return typeof body === "function" ? (body as (p: string) => unknown)(path) : body;
    if (path.startsWith("/api/schedules/S1/slots")) return slotsFor(path);
    if (path.startsWith("/api/schedules/S2/slots")) return slotsFor(path, "nights");
    if (path.startsWith("/api/schedules")) return { schedules: path.includes("agent_id=a2") ? [rooms] : path.includes("agent_id=a1") ? [clinic] : [clinic, rooms] };
    if (path.startsWith("/api/bookings")) {
      return { bookings: [{ id: "A5", name: "Priya Sharma", phone: "••••3210", service: null, specialty: null, reason: null, start: `${addDays(today(), 3)}T10:15:00+05:30`, end: `${addDays(today(), 3)}T10:30:00+05:30`, timezone: "Asia/Kolkata", status: "booked", conversation_id: null, receipt_id: null, agent_id: "a1", created_at: new Date().toISOString(), calendar_sync: "not_configured", schedule_id: "S1", schedule_name: "Doctor consultations" }], timezone: "Asia/Kolkata" };
    }
    throw new Error(`unexpected GET ${path}`);
  });
}
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

beforeEach(() => {
  mockSearch = "";
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
  (hooks.useAgents as jest.Mock).mockReturnValue({ data: agents, isLoading: false, mutate: jest.fn() });
  post.mockReset();
  del.mockReset();
  serve();
});

describe("Bookings overview", () => {
  it("shows a card for each schedule with hours in plain words and today's count", async () => {
    view(<BookingsPage />);
    expect(screen.getByRole("heading", { level: 1, name: /Bookings for/ })).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: /Doctor consultations/ });
    expect(link).toHaveAttribute("href", "/desk/bookings/S1");
    expect(within(link).getByText("Every day, 10 to 11 am")).toBeInTheDocument();
    expect(within(link).getByText("3 free, 1 booked")).toBeInTheDocument();
    expect(within(link).getByText("Nothing free soon")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Rooms/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^New schedule/ })).toHaveAttribute("href", "/desk/bookings/new");
  });

  it("makes every card as wide as its column, so cards in a list are the same width on a phone", async () => {
    view(<BookingsPage />);
    const cards = await screen.findAllByRole("link", { name: /Open calendar|New schedule/ });
    expect(cards.length).toBeGreaterThanOrEqual(3);
    for (const c of cards) expect(c.className).toMatch(/\bw-full\b/);
  });

  it("filters by front desk and remembers the choice", async () => {
    view(<BookingsPage />);
    await screen.findByRole("link", { name: /Doctor consultations/ });
    fireEvent.click(screen.getByRole("button", { name: /Front desk: All front desks/ }));
    fireEvent.click(screen.getByRole("option", { name: "Harbour Hotel" }));
    await waitFor(() => expect(screen.queryByRole("link", { name: /Doctor consultations/ })).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: /Rooms/ })).toBeInTheDocument();
    expect(localStorage.getItem("vakya-bookings-desk")).toBe("a2");
    expect(get).toHaveBeenCalledWith("/api/schedules?agent_id=a2");
    expect(screen.getByRole("link", { name: /^New schedule/ })).toHaveAttribute("href", "/desk/bookings/new?agent=a2");
  });

  it("starts on the remembered front desk", async () => {
    localStorage.setItem("vakya-bookings-desk", "a1");
    view(<BookingsPage />);
    expect(await screen.findByRole("button", { name: /Front desk: Sunrise Clinic/ })).toBeInTheDocument();
  });

  it("explains schedules in a sentence and offers to create one when there are none", async () => {
    serve({ "/api/schedules": { schedules: [] } });
    view(<BookingsPage />);
    expect(await screen.findByText("No schedules yet")).toBeInTheDocument();
    expect(screen.getByText(/A schedule says which days and hours callers can book/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Create a schedule/ })).toHaveAttribute("href", "/desk/bookings/new");
  });

  it("lists the upcoming bookings, with the schedule they are on", async () => {
    view(<BookingsPage />);
    expect(await screen.findByText(/Priya Sharma/)).toBeInTheDocument();
    expect(screen.getAllByText("Doctor consultations").length).toBeGreaterThan(1);
  });

  it("lists the upcoming bookings as an explorer list: Name, When, Schedule, Front desk, Source, Status", async () => {
    view(<BookingsPage />);
    const grid = await screen.findByRole("grid", { name: "Upcoming bookings" });
    expect(within(grid).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Name", "When", "Schedule", "Front desk", "Source", "Status"]);
    const row = await within(grid).findByRole("row", { name: /Priya Sharma/ });
    expect(within(row).getByText("Doctor consultations")).toBeInTheDocument();
    expect(within(row).getByText("Sunrise Clinic")).toBeInTheDocument();
    expect(within(row).getByText("Booked")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(row);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("opens the drawer of the booking named in ?booking=", async () => {
    mockSearch = "booking=A5";
    view(<BookingsPage />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Priya Sharma/)).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says quietly that a ?booking= it does not know was not found, and opens nothing", async () => {
    mockSearch = "booking=A99";
    view(<BookingsPage />);
    expect(await screen.findByRole("status")).toHaveTextContent(/Booking A99 was not found/);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("says in plain words when the schedules cannot be loaded", async () => {
    serve({ "/api/schedules": () => { throw new Error("boom"); } });
    view(<BookingsPage />);
    expect(await screen.findByText("Could not load the schedules")).toBeInTheDocument();
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
  });

  it("sends someone with no front desk and no schedule to set one up", async () => {
    (hooks.useAgents as jest.Mock).mockReturnValue({ data: [], isLoading: false, mutate: jest.fn() });
    serve({ "/api/schedules": { schedules: [] } });
    view(<BookingsPage />);
    expect(await screen.findByText("Set up a front desk first")).toBeInTheDocument();
  });

  it("still shows schedules that belong to no front desk", async () => {
    (hooks.useAgents as jest.Mock).mockReturnValue({ data: [], isLoading: false, mutate: jest.fn() });
    view(<BookingsPage />);
    expect(await screen.findByRole("link", { name: /Doctor consultations/ })).toBeInTheDocument();
  });
});

describe("Schedule calendar", () => {
  const open = async () => {
    view(<ScheduleView id="S1" />);
    await screen.findByRole("grid", { name: /Doctor consultations calendar/ });
    await waitFor(() => expect(document.querySelectorAll("[data-status]").length).toBeGreaterThan(0));
  };

  it("shows a week with a column per day, and a word with every state", async () => {
    await open();
    const calendar = screen.getByRole("grid", { name: /Doctor consultations calendar/ });
    expect(within(calendar).getAllByRole("columnheader")).toHaveLength(8); // the time column and seven days
    expect(get).toHaveBeenCalledWith(expect.stringContaining(`/api/schedules/S1/slots?from=${weekStart(today())}&to=${addDays(weekStart(today()), 6)}`));
    expect(document.querySelectorAll('[data-status="free"]').length).toBeGreaterThan(20);
    const full = document.querySelector<HTMLElement>('[data-status="full"]')!;
    expect(full).toHaveTextContent("Priya");
    expect(full.getAttribute("aria-label")).toMatch(/full, booked by Priya/);
    const busy = document.querySelector<HTMLElement>('[data-status="blocked"]')!;
    expect(busy).toHaveTextContent("Busy");
    expect(screen.getByLabelText("What the colours mean")).toHaveTextContent(/Free[\s\S]*Booked[\s\S]*Busy[\s\S]*Closed[\s\S]*Past/);
  });

  it("opens a popover on a free slot and marks it busy", async () => {
    post.mockResolvedValue({ block: { id: "K9" } });
    await open();
    fireEvent.click(document.querySelector<HTMLElement>('[data-status="free"]')!);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Free")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Mark busy" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/S1/blocks", expect.objectContaining({ start: expect.stringMatching(/T10:00:00\+05:30$/), end: expect.stringMatching(/T10:15:00\+05:30$/) })));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("lets a stretch be marked busy from the popover's 'until' list", async () => {
    post.mockResolvedValue({ block: { id: "K9" } });
    await open();
    const first = document.querySelector<HTMLElement>('[data-row="0"][data-status="free"]')!;
    fireEvent.click(first);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Busy until"), { target: { value: first.getAttribute("data-start")!.replace("10:00:00", "10:15:00") } });
    // the list holds the end of each following free slot; picking a later end marks them all
    fireEvent.click(within(dialog).getByRole("button", { name: "Mark busy" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
  });

  it("marks a range busy by pressing and dragging down a column, and Escape cancels", async () => {
    post.mockResolvedValue({ block: { id: "K9" } });
    await open();
    const cell = (row: number, col = 0) => document.querySelector<HTMLElement>(`[data-row="${row}"][data-col="${col}"]`)!;
    const under = jest.fn();
    document.elementFromPoint = under;
    // press on row 0, drag to row 2, release
    fireEvent.pointerDown(cell(0), { button: 0, pointerType: "mouse", clientX: 10, clientY: 10 });
    under.mockReturnValue(cell(2));
    fireEvent.pointerMove(window, { clientX: 10, clientY: 90, pointerType: "mouse" });
    await waitFor(() => expect(cell(1).getAttribute("aria-label")).toMatch(/free/)); // still the same slot underneath
    expect(cell(0)).toHaveTextContent("Busy 10 to 10:45 am");
    fireEvent.pointerUp(window, { pointerType: "mouse" });
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/schedules/S1/blocks", expect.objectContaining({ start: expect.stringMatching(/T10:00:00\+05:30$/), end: expect.stringMatching(/T10:45:00\+05:30$/) }))
    );
    // a press that is cancelled with Escape marks nothing
    post.mockClear();
    fireEvent.pointerDown(cell(0, 1), { button: 0, pointerType: "mouse" });
    under.mockReturnValue(cell(1, 1));
    fireEvent.pointerMove(window, { clientX: 10, clientY: 50, pointerType: "mouse" });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.pointerUp(window, { pointerType: "mouse" });
    expect(post).not.toHaveBeenCalled();
  });

  it("frees a busy block", async () => {
    del.mockResolvedValue(undefined);
    await open();
    fireEvent.click(document.querySelector<HTMLElement>('[data-status="blocked"]')!);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Free it again" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/schedules/S1/blocks/K1"));
  });

  it("books someone from a free slot", async () => {
    post.mockResolvedValue({ booking: { id: "A9" } });
    await open();
    fireEvent.click(document.querySelector<HTMLElement>('[data-status="free"]')!);
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Book someone" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Book" }));
    expect(await within(dialog).findByText("Type the name of the person you are booking.")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(/Name/), { target: { value: "Asha Rao" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Book" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/bookings", expect.objectContaining({ schedule_id: "S1", name: "Asha Rao" })));
  });

  it("puts the slot back and says so when marking busy fails", async () => {
    post.mockRejectedValue(new Error("HAS_BOOKINGS"));
    const { toast } = jest.requireMock("sonner");
    await open();
    const cell = document.querySelector<HTMLElement>('[data-status="free"]')!;
    const start = cell.getAttribute("data-start")!;
    fireEvent.click(cell);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Mark busy" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Could not mark that busy. Nothing was changed."));
    await waitFor(() => expect(document.querySelector(`[data-start="${start}"]`)).toHaveAttribute("data-status", "free"));
  });

  it("moves between slots with the arrow keys and opens one with Enter", async () => {
    await open();
    const first = document.querySelector<HTMLElement>('[data-row="0"][data-col="0"]')!;
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(document.activeElement).toHaveAttribute("data-row", "1");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(document.activeElement).toHaveAttribute("data-col", "1");
    // Escape closes the popover
    fireEvent.click(document.activeElement!);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("answers like a caller, in English or Hindi, and points at the two nearest times", async () => {
    post.mockResolvedValue({
      available: false,
      requested: { start: `${addDays(today(), 3)}T18:00:00+05:30`, end: `${addDays(today(), 3)}T18:15:00+05:30` },
      reason: "full",
      before: { start: `${addDays(today(), 3)}T10:00:00+05:30`, end: `${addDays(today(), 3)}T10:15:00+05:30` },
      after: { start: `${addDays(today(), 3)}T10:45:00+05:30`, end: `${addDays(today(), 3)}T11:00:00+05:30` },
      spoken: { en: "Thursday 6 pm is taken. I can do 10 am or 10:45 am.", hi: "गुरुवार शाम 6 बजे का समय भरा है।" },
    });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Is Thursday 6 pm free?" }));
    expect(await screen.findByText("Thursday 6 pm is taken. I can do 10 am or 10:45 am.")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/api/schedules/S1/check", { text: "Is Thursday 6 pm free?" });
    expect(screen.getByText("Already full")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "हिन्दी" }));
    expect(screen.getByText("गुरुवार शाम 6 बजे का समय भरा है।")).toBeInTheDocument();
    const later = screen.getByRole("button", { name: /Later/ });
    fireEvent.click(later);
    expect(later).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(document.querySelector(`[data-start="${addDays(today(), 3)}T10:45:00+05:30"]`)?.className).toMatch(/ring-2/));
  });

  it("says a time that is not where a slot starts is exactly that, and offers the two neighbours", async () => {
    const day = addDays(today(), 3);
    post.mockResolvedValue({
      available: false,
      requested: { start: `${day}T10:30:00+05:30`, end: `${day}T10:45:00+05:30` },
      reason: "not_a_start",
      before: { start: `${day}T10:15:00+05:30`, end: `${day}T10:30:00+05:30` },
      after: { start: `${day}T10:45:00+05:30`, end: `${day}T11:00:00+05:30` },
      spoken: { en: "We book every 15 minutes. 10:15 am or 10:45 am, which would suit you?", hi: "हम हर 15 मिनट पर बुकिंग करते हैं।" },
    });
    await open();
    fireEvent.change(screen.getByLabelText("What the caller says"), { target: { value: "is Thursday 10:30 am free?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("We book every 15 minutes. 10:15 am or 10:45 am, which would suit you?")).toBeInTheDocument();
    expect(screen.getByText("Not when a slot starts")).toBeInTheDocument();
    expect(screen.queryByText("Could not tell which time")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Earlier/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Later/ })).toBeInTheDocument();
  });

  it("shows a plain message when the check fails", async () => {
    post.mockRejectedValue(new Error("{\"detail\": 500}"));
    await open();
    fireEvent.change(screen.getByLabelText("What the caller says"), { target: { value: "kal shaam 6 baje" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("Could not check that. Try again in a moment.")).toBeInTheDocument();
  });

  it("goes to the next week and back to today", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Next week" }));
    const next = addDays(weekStart(today()), 7);
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining(`from=${next}`)));
    fireEvent.click(screen.getByRole("button", { name: "Today" }));
    await waitFor(() => expect(get.mock.calls.filter((c) => String(c[0]).includes(`from=${weekStart(today())}`)).length).toBeGreaterThan(1));
  });

  it("shows nights as a month with rooms left", async () => {
    view(<ScheduleView id="S2" />);
    expect(await screen.findByLabelText("What the caller says")).toHaveAttribute("placeholder", "Is Friday free for 2 nights?");
    await screen.findByRole("grid", { name: /Rooms nights/ });
    await waitFor(() => expect(document.querySelectorAll("[data-night]").length).toBeGreaterThan(20));
    expect(screen.getAllByText("7 left").length).toBeGreaterThan(10);
    expect(screen.getByText(/Click a night to block it/)).toBeInTheDocument();
  });

  it("says so when the schedule no longer exists", async () => {
    serve({ "/api/schedules": { schedules: [] } });
    view(<ScheduleView id="S9" />);
    expect(await screen.findByText("This schedule is not here any more")).toBeInTheDocument();
  });
});

describe("day view on a phone", () => {
  it("shows one day with a day switcher", async () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("max-width"), media: q, addEventListener: () => {}, removeEventListener: () => {} })) as unknown as typeof window.matchMedia;
    try {
      view(<ScheduleView id="S1" />);
      const calendar = await screen.findByRole("grid", { name: /calendar/ });
      expect(within(calendar).getAllByRole("columnheader")).toHaveLength(2);
      const switcher = screen.getByRole("group", { name: "Pick a day" });
      expect(within(switcher).getAllByRole("button")).toHaveLength(7);
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Next day" }));
      });
    } finally {
      window.matchMedia = original;
    }
  });
});

describe("Today's schedules", () => {
  it("links each schedule from the Today page, and stays quiet when there are none", async () => {
    const { unmount } = view(<ScheduleGlance />);
    const link = await screen.findByRole("link", { name: /Doctor consultations/ });
    expect(link).toHaveAttribute("href", "/desk/bookings/S1");
    expect(link).toHaveTextContent("3 free today");
    unmount();
    serve({ "/api/schedules": { schedules: [] } });
    const empty = view(<ScheduleGlance />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(empty.container).toBeEmptyDOMElement();
  });
});
