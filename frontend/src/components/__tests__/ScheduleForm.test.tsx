import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import NewSchedulePage from "@/app/(app)/desk/bookings/new/page";
import EditSchedulePage from "@/app/(app)/desk/bookings/[id]/edit/page";
import { ScheduleForm } from "@/components/schedules/ScheduleForm";
import { RangesEditor, addBreak } from "@/components/schedules/form/RangesEditor";
import { TimeField } from "@/components/schedules/form/TimeField";
import { api } from "@/lib/api";
import { weekFor, type Schedule } from "@/lib/schedules";

const push = jest.fn();
let params = new URLSearchParams();
jest.mock("next/navigation", () => ({
  useSearchParams: () => params,
  usePathname: () => "/desk/bookings/new",
  useRouter: () => ({ push, replace: jest.fn() }),
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
import { ApiError } from "@/lib/apiBase";

const get = api.get as jest.Mock;
const post = api.post as jest.Mock;
const put = api.put as jest.Mock;
const del = api.del as jest.Mock;

const agents = [
  { id: "a1", name: "Sunrise Clinic", greeting: "", system_instructions: "", supported_languages: ["en"], voice_id: null, created_at: "", document_count: 0 },
  { id: "a2", name: "Harbour Hotel", greeting: "", system_instructions: "", supported_languages: ["en"], voice_id: null, created_at: "", document_count: 0 },
];
const stored: Schedule = {
  id: "S1", agent_id: "a1", name: "Doctor consultations", description: null, kind: "slots", accent: "violet", timezone: "Asia/Kolkata",
  slot_minutes: 15, buffer_minutes: 0, capacity: 1,
  week: weekFor(["mon", "tue", "wed", "thu", "fri", "sat"], [{ start: "10:00", end: "13:00" }, { start: "17:00", end: "20:00" }]),
  check_in: null, check_out: null, min_notice_minutes: 60, horizon_days: 30, closed_dates: [],
};
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);
const hours = () => screen.getByRole("region", { name: "What hours?" });

beforeEach(() => {
  params = new URLSearchParams();
  push.mockReset();
  post.mockReset();
  put.mockReset();
  del.mockReset();
  get.mockReset().mockImplementation(async (p: string) => {
    if (p.startsWith("/api/schedules")) return { schedules: [stored] };
    throw new Error(`unexpected GET ${p}`);
  });
  (hooks.useAgents as jest.Mock).mockReturnValue({ data: agents, isLoading: false, mutate: jest.fn() });
});

describe("TimeField and RangesEditor", () => {
  it("takes a typed time and keeps it as 24-hour text", () => {
    const onChange = jest.fn();
    render(<TimeField label="Open from" value="10:00" onChange={onChange} />);
    const input = screen.getByLabelText("Open from");
    expect(input).toHaveValue("10 am");
    fireEvent.change(input, { target: { value: "5:30pm" } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith("17:30");
  });
  it("reads a bare end hour after the start, and refuses nonsense with a hint", () => {
    const onChange = jest.fn();
    render(<TimeField label="Until" value="13:00" after="09:00" onChange={onChange} />);
    const input = screen.getByLabelText("Until");
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("17:00");
    fireEvent.change(input, { target: { value: "tea time" } });
    fireEvent.blur(input);
    expect(screen.getByText("Type a time like 9 am or 5:30 pm.")).toBeInTheDocument();
  });
  it("adds a break by splitting a long session, and shows it", () => {
    expect(addBreak([{ start: "09:00", end: "18:00" }])).toEqual([{ start: "09:00", end: "13:00" }, { start: "14:00", end: "18:00" }]);
    expect(addBreak([{ start: "09:00", end: "11:00" }])[1].start).toBe("12:00");
    const onChange = jest.fn();
    render(<RangesEditor ranges={[{ start: "10:00", end: "13:00" }, { start: "17:00", end: "21:00" }]} onChange={onChange} />);
    expect(screen.getByText("Break, 1 to 5 pm")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove session 2" }));
    expect(onChange).toHaveBeenCalledWith([{ start: "10:00", end: "13:00" }]);
  });
});

describe("New schedule", () => {
  it("opens with the seven days showing and the quick choices", async () => {
    view(<ScheduleForm />);
    expect(screen.getByRole("heading", { level: 1, name: "New schedule" })).toBeInTheDocument();
    const days = screen.getByRole("group", { name: "Days you are open" });
    expect(within(days).getAllByRole("button")).toHaveLength(7);
    expect(within(days).getByRole("button", { name: "Monday" })).toHaveAttribute("aria-pressed", "true");
    expect(within(days).getByRole("button", { name: "Sunday" })).toHaveAttribute("aria-pressed", "false");
    for (const label of ["All days", "Weekdays", "Mon to Sat", "Weekends", "Closed"]) expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mon to Sat" })).toHaveAttribute("aria-pressed", "true");
  });

  it("changes the days with a preset and updates the preview at once", () => {
    view(<ScheduleForm />);
    const preview = screen.getByRole("region", { name: "Live preview" });
    expect(preview).toHaveTextContent("Open Monday to Saturday, 10 am to 1 pm and 5 to 8 pm");
    fireEvent.click(screen.getByRole("button", { name: "Weekends" }));
    expect(preview).toHaveTextContent("Open Saturday and Sunday, 10 am to 1 pm and 5 to 8 pm");
    fireEvent.click(screen.getByRole("button", { name: "Closed" }));
    expect(preview).toHaveTextContent("Closed every day");
    fireEvent.click(screen.getByRole("button", { name: "All days" }));
    expect(preview).toHaveTextContent("Open every day");
    fireEvent.click(screen.getByRole("button", { name: "Tuesday" }));
    expect(preview).toHaveTextContent("Open Monday and Wednesday to Sunday");
  });

  it("applies a quick hours preset", () => {
    view(<ScheduleForm />);
    fireEvent.click(within(hours()).getByRole("button", { name: "9 to 5" }));
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("9 am to 5 pm");
    fireEvent.click(within(hours()).getByRole("button", { name: "Open 24 hours" }));
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("all day");
  });

  it("shows which hours preset is chosen, in the same style as the day chips", () => {
    view(<ScheduleForm />);
    const nine = within(hours()).getByRole("button", { name: "9 to 5" });
    fireEvent.click(nine);
    expect(nine).toHaveAttribute("aria-pressed", "true");
    expect(nine.className).toContain("var(--chip-on-bg)");
    const day = within(hours()).getByRole("button", { name: "Open 24 hours" });
    expect(day).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(day);
    expect(day).toHaveAttribute("aria-pressed", "true");
    expect(nine).toHaveAttribute("aria-pressed", "false");
  });

  it("explains a session that ends before it starts, in words", () => {
    view(<ScheduleForm />);
    const end = within(hours()).getAllByLabelText(/until, session 2/)[0];
    fireEvent.change(end, { target: { value: "4 pm" } });
    fireEvent.blur(end);
    expect(within(hours()).getByRole("alert")).toHaveTextContent("The evening session ends before it starts.");
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("Finish the highlighted parts");
  });

  it("edits each day when the hours differ, and copies one day to all", () => {
    view(<ScheduleForm />);
    fireEvent.click(within(hours()).getByRole("switch", { name: "Same hours every open day" }));
    const monday = screen.getByText("Monday", { selector: "p" }).closest("li")!;
    const until = within(monday).getAllByLabelText(/until, session 2/)[0];
    fireEvent.change(until, { target: { value: "9 pm" } });
    fireEvent.blur(until);
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("Monday, 10 am to 1 pm and 5 to 9 pm; Tuesday to Saturday, 10 am to 1 pm and 5 to 8 pm");
    fireEvent.click(within(monday).getByRole("button", { name: "Copy to all days" }));
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("Open Monday to Saturday, 10 am to 1 pm and 5 to 9 pm");
    expect(within(hours()).getByText("Sunday", { selector: "p" }).closest("li")).toHaveTextContent("Closed");
  });

  it("fills the form from a sentence, highlights what changed, and undoes it", async () => {
    post.mockResolvedValue({
      week: weekFor(["mon", "tue", "wed", "thu", "fri"], [{ start: "09:00", end: "12:00" }, { start: "14:00", end: "18:00" }]),
      slot_minutes: 30, closed_dates: [], notes: ["Sunday is closed."], confident: true,
    });
    view(<ScheduleForm />);
    fireEvent.change(screen.getByLabelText(/Or just describe it/), { target: { value: "Mon to Fri 9 to 12 and 2 to 6, half hour slots" } });
    fireEvent.click(screen.getByRole("button", { name: "Fill it in" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/schedules/parse-hours", { text: "Mon to Fri 9 to 12 and 2 to 6, half hour slots" }));
    expect(await screen.findByText(/Updated the days, the hours and the slot length/)).toBeInTheDocument();
    const preview = screen.getByRole("region", { name: "Live preview" });
    expect(preview).toHaveTextContent("Open Monday to Friday, 9 am to noon and 2 to 6 pm. 30-minute slots");
    expect(screen.getByRole("region", { name: "What hours?" }).className).toMatch(/ring-2/);
    fireEvent.click(screen.getByRole("button", { name: /Undo/ }));
    expect(preview).toHaveTextContent("Open Monday to Saturday, 10 am to 1 pm and 5 to 8 pm. 15-minute slots");
    expect(screen.getByRole("region", { name: "What hours?" }).className).not.toMatch(/ring-2/);
  });

  it("says in plain words when the sentence cannot be read", async () => {
    post.mockRejectedValue(new ApiError("{\"x\":1}", 500, "http_500"));
    view(<ScheduleForm />);
    fireEvent.change(screen.getByLabelText(/Or just describe it/), { target: { value: "whenever" } });
    fireEvent.click(screen.getByRole("button", { name: "Fill it in" }));
    expect(await screen.findByText(/Could not read that/)).toBeInTheDocument();
  });

  it("offers slot lengths, a custom one, rooms for nights and holidays", () => {
    view(<ScheduleForm />);
    const slots = screen.getByRole("region", { name: "How are slots booked?" });
    const lengths = within(slots).getByRole("radiogroup", { name: "Slot length in minutes" });
    expect(within(lengths).getAllByRole("radio").map((r) => r.textContent)).toEqual(["10 min", "15 min", "20 min", "30 min", "45 min", "60 min", "Custom"]);
    fireEvent.click(within(lengths).getByRole("radio", { name: "45 min" }));
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("45-minute slots");
    fireEvent.click(within(lengths).getByRole("radio", { name: "Custom" }));
    expect(within(slots).getByRole("group", { name: "Custom slot length" })).toBeInTheDocument();
    fireEvent.click(within(slots).getByRole("button", { name: "More: How many at the same time" }));
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("up to 2 at the same time");
    fireEvent.change(within(slots).getByLabelText("Holidays"), { target: { value: "2026-11-12" } });
    fireEvent.click(within(slots).getByRole("button", { name: "Add holiday" }));
    expect(within(slots).getByText(/12 Nov/)).toBeInTheDocument();
    fireEvent.click(within(slots).getByRole("button", { name: /Remove holiday/ }));
    expect(within(slots).queryByText(/12 Nov/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /Nightly stays/ }));
    expect(screen.getByRole("region", { name: "Rooms and rules" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Rooms and rules" })).getByRole("group", { name: "Rooms" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Check-in and check-out" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("Nightly stays");
  });

  it("will not save without a name, and says what to fix", async () => {
    view(<ScheduleForm />);
    fireEvent.click(screen.getByRole("button", { name: "Create schedule" }));
    expect((await screen.findAllByText(/Give this schedule a name/)).length).toBeGreaterThan(0);
    expect(post).not.toHaveBeenCalled();
  });

  it("creates the schedule for the chosen front desk and opens its calendar", async () => {
    params = new URLSearchParams("agent=a2");
    post.mockResolvedValue({ schedule: { ...stored, id: "S7" } });
    view(<ScheduleForm />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Doctor consultations" } });
    fireEvent.click(screen.getByRole("button", { name: "Create schedule" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    const [path, body] = post.mock.calls[0];
    expect(path).toBe("/api/schedules");
    expect(body).toMatchObject({
      agent_id: "a2", name: "Doctor consultations", kind: "slots", slot_minutes: 15, capacity: 1, check_in: null,
      week: { mon: [{ start: "10:00", end: "13:00" }, { start: "17:00", end: "20:00" }], sun: [] },
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/desk/bookings/S7"));
  });

  it("keeps the form and says so when saving fails", async () => {
    post.mockRejectedValue(new ApiError("Request failed", 500, "http_500"));
    view(<ScheduleForm />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Rooms" } });
    fireEvent.click(screen.getByRole("button", { name: "Create schedule" }));
    expect(await screen.findByText(/Could not save the schedule/)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("renders from the route", async () => {
    view(<NewSchedulePage />);
    expect(await screen.findByRole("heading", { level: 1, name: "New schedule" })).toBeInTheDocument();
  });
});

describe("Edit schedule", () => {
  it("opens with what is stored and saves with PUT", async () => {
    put.mockResolvedValue({ schedule: stored });
    view(<EditSchedulePage />);
    expect(await screen.findByRole("heading", { level: 1, name: "Edit Doctor consultations" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Doctor consultations");
    expect(screen.getByRole("region", { name: "Live preview" })).toHaveTextContent("Open Monday to Saturday, 10 am to 1 pm and 5 to 8 pm");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(put).toHaveBeenCalledWith("/api/schedules/S1", expect.objectContaining({ name: "Doctor consultations", agent_id: "a1" })));
  });

  it("asks before deleting, and offers to force when bookings remain", async () => {
    del.mockRejectedValueOnce(new ApiError("3 upcoming bookings use this schedule", 409, "HAS_BOOKINGS")).mockResolvedValueOnce(undefined);
    view(<EditSchedulePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete schedule" }));
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Yes, delete it" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/schedules/S1"));
    expect(await screen.findByText(/People are still booked on this schedule/)).toBeInTheDocument();
    expect(screen.queryByText(/HAS_BOOKINGS/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete it and its bookings" }));
    await waitFor(() => expect(del).toHaveBeenLastCalledWith("/api/schedules/S1?force=1"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/desk/bookings"));
  });

  it("says so when the schedule is gone", async () => {
    get.mockImplementation(async () => ({ schedules: [] }));
    view(<EditSchedulePage />);
    expect(await screen.findByText("This schedule is not here any more")).toBeInTheDocument();
  });
});
