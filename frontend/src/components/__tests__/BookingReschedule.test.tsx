import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { toast } from "sonner";
import { BookingDrawer } from "@/components/schedules/BookingDrawer";
import { RescheduleForm } from "@/components/schedules/RescheduleForm";
import type { Booking } from "@/components/desk/calls";
import { api } from "@/lib/api";

jest.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(), usePathname: () => "/", useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), patch: jest.fn(), del: jest.fn(), blob: jest.fn() },
  ApiError: jest.requireActual("@/lib/apiBase").ApiError,
  API_URL: "http://api.test",
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const { ApiError } = jest.requireActual("@/lib/apiBase");
const get = api.get as jest.Mock;
const patch = api.patch as jest.Mock;
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

const booking: Booking = {
  id: "A12", callId: "c1", name: "Priya Sharma", phone: "••••3210", phoneMasked: "••••3210", service: null, reason: null, start: "2026-11-05T15:00:00+05:30",
  timezone: "Asia/Kolkata", createdAt: "2026-11-04T10:00:00+05:30", status: "booked", calendar: "synced", agentName: "Sunrise Clinic", agentId: "a1",
  scheduleId: "S1", scheduleName: "Doctor consultations", end: null, nights: null, source: "phone", callerRef: null, callerBookingsCount: 1, language: "hi",
  receiptId: null, hasEvidence: false, practice: false, lines: [], fromCalls: false,
};

beforeEach(() => {
  get.mockReset().mockResolvedValue({ bookings: [], timezone: "Asia/Kolkata" });
  patch.mockReset().mockResolvedValue({ booking: {} });
  (toast.success as jest.Mock).mockClear();
});

describe("moving a booking from the desk", () => {
  it("offers Move next to Cancel and moves the booking to the new day and time", async () => {
    const onChanged = jest.fn();
    view(<BookingDrawer booking={booking} onClose={jest.fn()} onChanged={onChanged} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Cancel booking" })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Move" }));
    const form = within(dialog).getByRole("form", { name: "Move booking A12" });
    expect(within(form).getByLabelText("Day")).toHaveValue("2026-11-05");
    expect(within(form).getByLabelText("Time")).toHaveValue("15:00");
    fireEvent.change(within(form).getByLabelText("Time"), { target: { value: "16:00" } });
    fireEvent.click(within(form).getByRole("button", { name: "Move booking" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/bookings/A12", { start: "2026-11-05T16:00" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalledWith("Booking A12 moved");
  });

  it("shows where a moved booking was before", async () => {
    view(<BookingDrawer booking={{ ...booking, start: "2026-11-05T16:00:00+05:30", movedFrom: { date: "2026-11-05", time: "15:00" } }} onClose={jest.fn()} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Moved from Thu 5 Nov, 3 pm")).toBeInTheDocument();
  });

  it("does not offer Move on a cancelled booking", async () => {
    view(<BookingDrawer booking={{ ...booking, status: "cancelled" }} onClose={jest.fn()} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: "Move" })).toBeNull();
  });

  it("explains a taken time and lets one tap use the nearest free time", async () => {
    patch.mockRejectedValueOnce(
      new ApiError("Thursday at 4 pm is already taken.", 409, "SLOT_UNAVAILABLE", {
        reason: "full",
        before: { start: "2026-11-05T15:00:00+05:30", end: "2026-11-05T16:00:00+05:30" },
        after: { start: "2026-11-05T17:00:00+05:30", end: "2026-11-05T18:00:00+05:30" },
        spoken: { en: "Thursday at 4 pm is already taken. 3 pm or 5 pm, which would suit you?", hi: "" },
      })
    );
    const onMoved = jest.fn();
    view(<RescheduleForm booking={booking} onMoved={onMoved} onCancel={jest.fn()} />);
    fireEvent.change(screen.getByLabelText("Time"), { target: { value: "16:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Move booking" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Thursday at 4 pm is already taken.");
    expect(onMoved).not.toHaveBeenCalled();
    fireEvent.click(within(alert).getByRole("button", { name: "Use Thu 5 Nov, 5 pm" }));
    expect(screen.getByLabelText("Time")).toHaveValue("17:00");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows any other failure in words and keeps the form", async () => {
    patch.mockRejectedValueOnce(new ApiError("Booking A12 is already cancelled.", 409, "ALREADY_CANCELLED"));
    view(<RescheduleForm booking={booking} onMoved={jest.fn()} onCancel={jest.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Move booking" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Booking A12 is already cancelled.");
    expect(screen.getByRole("button", { name: "Move booking" })).toBeEnabled();
  });

  it("asks only for the check-in day for a stay", async () => {
    view(<RescheduleForm booking={{ ...booking, nights: 2, start: "2026-11-10T14:00:00+05:30" }} onMoved={jest.fn()} onCancel={jest.fn()} />);
    expect(screen.queryByLabelText("Time")).toBeNull();
    fireEvent.change(screen.getByLabelText("Check-in"), { target: { value: "2026-11-12" } });
    fireEvent.click(screen.getByRole("button", { name: "Move booking" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/bookings/A12", { start: "2026-11-12T00:00" }));
  });
});
