import { alertText, alertable, freshBookings, summaryText } from "@/lib/deskAlerts";
import type { Booking } from "@/components/desk/calls";

function booking(id: string, createdAt: string, over: Partial<Booking> = {}): Booking {
  return {
    id, callId: null, name: "Sunita Kulkarni", phone: null, service: "Dr. Joshi", reason: null, start: "2026-11-12T13:30:00+05:30",
    timezone: "Asia/Kolkata", createdAt, status: "booked", calendar: null, agentName: null, practice: false, lines: [], fromCalls: false,
    source: "phone", ...over,
  };
}

describe("booking alerts", () => {
  it("tells nothing on the first look, and remembers the newest booking", () => {
    const r = freshBookings([booking("A1", "2026-11-06T09:00:00Z"), booking("A2", "2026-11-06T10:00:00Z")], null);
    expect(r.fresh).toEqual([]);
    expect(r.seen).toBe("2026-11-06T10:00:00Z");
  });

  it("returns only what is newer than the mark, oldest first", () => {
    const list = [booking("A3", "2026-11-06T12:00:00Z"), booking("A1", "2026-11-06T09:00:00Z"), booking("A2", "2026-11-06T11:00:00Z")];
    const r = freshBookings(list, "2026-11-06T10:00:00Z");
    expect(r.fresh.map((b) => b.id)).toEqual(["A2", "A3"]);
    expect(r.seen).toBe("2026-11-06T12:00:00Z");
  });

  it("compares instants, not strings, across offsets", () => {
    // 15:00 +05:30 is 09:30 UTC: older than the mark, so not new.
    const r = freshBookings([booking("A1", "2026-11-06T15:00:00+05:30")], "2026-11-06T10:00:00Z");
    expect(r.fresh).toEqual([]);
    expect(r.seen).toBe("2026-11-06T10:00:00Z");
  });

  it("keeps the mark when nothing is new, and ignores a broken mark", () => {
    expect(freshBookings([], "2026-11-06T10:00:00Z")).toEqual({ fresh: [], seen: "2026-11-06T10:00:00Z" });
    expect(freshBookings([booking("A1", "2026-11-06T09:00:00Z")], "garbage").fresh).toEqual([]);
  });

  it("leaves out staff additions, cancellations and practice calls", () => {
    expect(alertable(booking("A1", "2026-11-06T09:00:00Z"))).toBe(true);
    expect(alertable(booking("A1", "2026-11-06T09:00:00Z", { source: "staff" }))).toBe(false);
    expect(alertable(booking("A1", "2026-11-06T09:00:00Z", { status: "cancelled" }))).toBe(false);
    expect(alertable(booking("A1", "2026-11-06T09:00:00Z", { practice: true }))).toBe(false);
    expect(alertable(booking("A1", "2026-11-06T09:00:00Z", { source: "missed_call" }))).toBe(true);
  });

  it("writes the alert the way the desk writes a booking", () => {
    expect(alertText(booking("A7", "2026-11-06T09:00:00Z"))).toEqual({
      title: "New booking: Sunita Kulkarni",
      body: "Thu 12 Nov, 1:30 pm · Dr. Joshi",
      url: "/desk/bookings/A7",
      tag: "booking-A7",
    });
    expect(alertText(booking("A8", "2026-11-06T09:00:00Z", { name: null, service: null, scheduleName: "OPD" })).title).toBe("New booking: a caller");
    expect(summaryText(5).title).toBe("5 new bookings");
  });
});
