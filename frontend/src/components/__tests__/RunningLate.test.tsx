import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { LateBanner } from "@/components/queue/LateBanner";
import { RunningLate } from "@/components/queue/RunningLate";
import { TodayQueue } from "@/components/queue/TodayQueue";
import { TokenBoard } from "@/components/queue/TokenBoard";
import { bookedByWords, callError, expectedBySlot, lateWords, runningWords, type QueueToday, type QueueToken } from "@/components/queue/queue";
import { fromApiBooking, withTokens, type ApiBooking } from "@/components/desk/calls";
import { BookingList } from "@/components/desk/BookingList";
import { api, ApiError } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
  ApiError: jest.requireActual("@/lib/apiBase").ApiError,
  API_URL: "http://api.test",
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
import { toast } from "sonner";

const get = api.get as jest.Mock;
const post = api.post as jest.Mock;

const token = (over: Partial<QueueToken>): QueueToken => ({
  booking_id: "A1", token: 1, name: "Asha Rao", phone_masked: "••••0011", source: "phone", slot_start: "18:00", slot_end: "18:30",
  expected_at: "18:40", status: "waiting", people_ahead: 1, moved_minutes: 40, can_call: true, ...over,
});
const queue = (over: Partial<QueueToday> = {}): QueueToday => ({
  schedule_id: "S1", schedule_name: "Doctor consultations", date: "2026-11-03", slot_minutes: 30, minutes_late: 40, reason: "emergency surgery",
  set_at: null, now_serving: 7, count: 2, calling: { ready: true, missing: [] },
  tokens: [token({ booking_id: "A1", token: 7, name: "Asha Rao", status: "now", moved_minutes: 0, expected_at: "18:00" }), token({ booking_id: "A2", token: 8, name: "Bharat Patil", slot_start: "18:30", expected_at: "19:10" })],
  affected: [{ ...token({ booking_id: "A2", token: 8, name: "Bharat Patil", slot_start: "18:30", expected_at: "19:10" }), suggested_time: "19:10" }],
  ...over,
});

const isNowSeeing = (_: string, el: Element | null) => el?.tagName === "P" && /^Now seeing token/.test(el.textContent ?? "");
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

beforeEach(() => {
  jest.clearAllMocks();
});

describe("the words", () => {
  it("says how late in plain words", () => {
    expect(lateWords(0)).toBe("On time");
    expect(lateWords(40)).toBe("40 min late");
    expect(lateWords(60)).toBe("1 hour late");
    expect(lateWords(80)).toBe("1 h 20 min late");
    expect(runningWords(40)).toBe("Running 40 min late");
  });

  it("names who booked it for someone", () => {
    expect(bookedByWords({ booked_by: "Rahul", relation: "mother" })).toBe("booked by Rahul, for their mother");
    expect(bookedByWords({ relation: "father" })).toBe("booked by the caller, for their father");
    expect(bookedByWords({ booked_by: "Rahul" })).toBe("booked by Rahul");
    expect(bookedByWords({})).toBeNull();
  });

  it("turns a failed call into a sentence", () => {
    expect(callError(new ApiError("x", 409, "CALLING_NOT_SET_UP"))).toMatch(/Calling is not set up yet/);
    expect(callError(new ApiError("Too many", 429, "RATE_LIMITED"))).toMatch(/Too many calls this hour/);
    expect(callError(new ApiError('{"detail":1}', 500, "X"))).toBe("Could not place the call. Tell the patient yourself.");
    expect(callError(new Error("boom"))).toBe("Could not place the call. Tell the patient yourself.");
  });

  it("lists the calendar's expected times for the bookings that moved", () => {
    expect(expectedBySlot(queue())).toEqual({ "18:30": "7:10 pm" });
    expect(expectedBySlot(undefined)).toEqual({});
  });
});

describe("tokens of the bookings list", () => {
  const api_ = (id: string, start: string, over: Partial<ApiBooking> = {}): ApiBooking => ({
    id, name: "N " + id, phone: null, service: null, specialty: null, reason: null, start, end: null, timezone: "Asia/Kolkata", status: "booked",
    conversation_id: null, receipt_id: null, agent_id: null, created_at: null, calendar_sync: "not_configured", schedule_id: "S1", schedule_name: "Doctor", ...over,
  });
  const now = new Date("2026-11-03T04:00:00Z");

  it("numbers today's and later days' live time-slot bookings by start, a cancellation renumbers", () => {
    const list = [
      api_("A3", "2026-11-03T19:00:00+05:30"), api_("A1", "2026-11-03T18:00:00+05:30"), api_("A2", "2026-11-03T18:30:00+05:30"),
      api_("A4", "2026-11-04T18:00:00+05:30"), api_("A5", "2026-11-02T18:00:00+05:30"), api_("A6", "2026-11-03T20:00:00+05:30", { nights: 2 }),
    ].map((b) => fromApiBooking(b));
    const by = Object.fromEntries(withTokens(list, now).map((b) => [b.id, b.token]));
    expect(by).toEqual({ A1: 1, A2: 2, A3: 3, A4: 1, A5: null, A6: null });
    const without = withTokens(list.map((b) => (b.id === "A1" ? { ...b, status: "cancelled" as const } : b)), now);
    expect(Object.fromEntries(without.map((b) => [b.id, b.token]))).toMatchObject({ A2: 1, A3: 2 });
  });

  it("shows the token and who booked it", () => {
    const [b] = withTokens([fromApiBooking({ ...api_("A1", "2026-11-03T18:00:00+05:30", { booked_by: "Rahul", relation: "mother", name: "Sunita Kulkarni" }) })], now);
    view(<BookingList bookings={[b]} emptyTitle="none" emptyText="none" />);
    expect(screen.getByText("Token 1")).toBeInTheDocument();
    expect(screen.getByText("booked by Rahul, for their mother")).toBeInTheDocument();
  });
});

describe("Running late", () => {
  it("one tap sets the delay with the typed reason and says so", async () => {
    post.mockResolvedValue(queue({ minutes_late: 30 }));
    view(<RunningLate scheduleId="S1" queue={queue({ minutes_late: 0, reason: null })} />);
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "stuck in surgery" } });
    fireEvent.click(screen.getByRole("button", { name: "30 min" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/S1/delay", { minutes_late: 30, reason: "stuck in surgery" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("30 min late")));
  });

  it("On time clears it, and the chosen chip is pressed", async () => {
    post.mockResolvedValue(queue({ minutes_late: 0, reason: null }));
    view(<RunningLate scheduleId="S1" queue={queue({ minutes_late: 45 })} />);
    expect(screen.getByRole("button", { name: "45 min" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "On time" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/S1/delay", { minutes_late: 0, reason: null }));
  });

  it("takes a custom number of minutes between 1 and 480", async () => {
    post.mockResolvedValue(queue({ minutes_late: 25 }));
    view(<RunningLate scheduleId="S1" queue={queue({ minutes_late: 0, reason: null })} />);
    fireEvent.click(screen.getByRole("button", { name: "Other" }));
    const set = screen.getByRole("button", { name: "Set" });
    expect(set).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Minutes late"), { target: { value: "500" } });
    expect(set).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Minutes late"), { target: { value: "25" } });
    fireEvent.click(set);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/S1/delay", { minutes_late: 25, reason: null }));
  });

  it("tells the desk in plain words when it could not save", async () => {
    post.mockRejectedValue(new ApiError("down", 0));
    view(<RunningLate scheduleId="S1" queue={queue({ minutes_late: 0 })} />);
    fireEvent.click(screen.getByRole("button", { name: "15 min" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/Could not reach the server/)));
  });
});

describe("The token board", () => {
  it("shows who is being seen, each token with when, and moves on with Next", async () => {
    post.mockResolvedValue(queue({ now_serving: 8 }));
    view(<TokenBoard scheduleId="S1" queue={queue()} />);
    expect(screen.getByText(isNowSeeing)).toHaveTextContent("Now seeing token 7 · Asha Rao");
    const list = screen.getByRole("list", { name: "Today's tokens" });
    expect(within(list).getByText("In with the doctor")).toBeInTheDocument();
    expect(within(list).getByText("About 7:10 pm")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Next/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/S1/now-serving", { token: "next" }));
    const mark = screen.getByRole("button", { name: "Mark token 8, Bharat Patil, as now seeing" });
    await waitFor(() => expect(mark).toBeEnabled());
    fireEvent.click(mark);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/S1/now-serving", { token: 8 }));
  });

  it("lists the patients who moved and calls one to let them know", async () => {
    post.mockResolvedValue({ status: "calling", to: "••••0011" });
    view(<TokenBoard scheduleId="S1" queue={queue()} />);
    const moved = screen.getByRole("region", { name: "Moved by 15 minutes or more" });
    expect(within(moved).getByText(/6:30 pm is now around/)).toHaveTextContent("6:30 pm is now around 7:10 pm");
    fireEvent.click(within(moved).getByRole("button", { name: "Call Bharat Patil to let them know" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/S1/notify/A2", {}));
    expect(await within(moved).findByText("Calling")).toBeInTheDocument();
  });

  it("says in plain words when calling is not set up", async () => {
    post.mockRejectedValue(new ApiError("Phone calling is not set up: add VOBIZ_AUTH_ID to backend/.env. Until then", 409, "CALLING_NOT_SET_UP"));
    view(<TokenBoard scheduleId="S1" queue={queue({ calling: { ready: false, missing: ["VOBIZ_AUTH_ID"] } })} />);
    expect(screen.getByText(/Calling is not set up yet, so the buttons above will not place a call/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Call Bharat Patil to let them know" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Calling is not set up yet, so please tell this patient yourself."));
  });

  it("adds a walk-in", async () => {
    post.mockResolvedValue({ booking: { booking_id: "A9", token: 9, time: "18:30" }, today: queue({ count: 3 }) });
    view(<TokenBoard scheduleId="S1" queue={queue()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a walk-in" }));
    expect(screen.getByRole("button", { name: "Add walk-in" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ganesh Pawar" } });
    fireEvent.click(screen.getByRole("button", { name: "Add walk-in" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/S1/walk-in", { name: "Ganesh Pawar" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Added Ganesh Pawar as token 9"));
  });

  it("does not list anyone when the delay is small", () => {
    view(<TokenBoard scheduleId="S1" queue={queue({ affected: [] })} />);
    expect(screen.getByText(/Nobody's time has moved that much yet/)).toBeInTheDocument();
  });
});

describe("The Today queue and the calendar banner", () => {
  it("shows a control and board for each time-slot schedule and none for stays", async () => {
    get.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/schedules?")) return { schedules: [{ id: "S1", name: "Doctor consultations", kind: "slots" }, { id: "S2", name: "Rooms", kind: "nights" }] };
      if (path === "/api/schedules") return { schedules: [{ id: "S1", name: "Doctor consultations", kind: "slots" }, { id: "S2", name: "Rooms", kind: "nights" }] };
      if (path === "/api/schedules/S1/today") return queue();
      throw new Error("unexpected " + path);
    });
    view(<TodayQueue />);
    expect(await screen.findByRole("group", { name: "How late are you running today" })).toBeInTheDocument();
    expect(await screen.findByText("Running 40 min late")).toBeInTheDocument();
    expect(screen.queryByText("Rooms")).not.toBeInTheDocument();
    expect(await screen.findByText(isNowSeeing)).toBeInTheDocument();
  });

  it("the banner appears only while late, and Change opens the control", () => {
    const { rerender } = view(<LateBanner scheduleId="S1" queue={queue({ minutes_late: 0 })} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    rerender(<SWRConfig value={{ provider: () => new Map() }}><LateBanner scheduleId="S1" queue={queue()} /></SWRConfig>);
    expect(screen.getByRole("status")).toHaveTextContent("Running 40 min late");
    expect(screen.getByRole("status")).toHaveTextContent("emergency surgery");
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    expect(screen.getByRole("group", { name: "How late are you running today" })).toBeInTheDocument();
  });
});
