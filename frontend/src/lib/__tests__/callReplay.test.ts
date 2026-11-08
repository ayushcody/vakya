import { describeAction, turnsToEvents } from "../callReplay";
import { whatsappChip, calendarChip } from "@/components/live/Delivery";

describe("describeAction", () => {
  it("reads a taken check, a read-back refusal, a booking and a cancellation", () => {
    expect(describeAction({ name: "check_availability", ok: true, summary: "15:00 on 2026-11-10 is not free, 2 other option(s)" })).toMatchObject({ status: "taken", available: false });
    expect(describeAction({ name: "book", ok: false, summary: "failed: NOT DONE: first read this back to the caller" }).status).toBe("readback");
    expect(describeAction({ name: "book", ok: true, summary: "booking A28 on Tuesday 2026-11-10 17:00" })).toMatchObject({ status: "booked", booking_id: "A28", label: "Booked A28 · Tue 10 Nov, 5 pm" });
    expect(describeAction({ name: "cancel", ok: true, summary: "cancelled A27 on Monday 2026-11-09 17:00" }).status).toBe("cancelled");
  });
});

describe("turnsToEvents", () => {
  it("puts actions before the agent's words and skips the connect marker", () => {
    const ev = turnsToEvents(
      [
        { role: "caller", text: "[call connected]", at: null, actions: [] },
        { role: "caller", text: "Book me Tuesday", at: "2026-11-08T12:00:00Z", actions: [] },
        { role: "agent", text: "Done", at: "2026-11-08T12:00:02Z", actions: [{ name: "book", ok: true, summary: "booking A1 on Tuesday 2026-11-10 17:00" }] },
      ],
      { agent_id: "a", conversation_id: "c" }
    );
    expect(ev.map((e) => e.type)).toEqual(["caller", "tool", "agent"]);
  });
});

describe("delivery chips", () => {
  const d = { messages: [{ at: "2026-11-08T12:00:05Z", state: "sent", kind: "booked", booking_id: "A1" }], bookings: [{ id: "A1", calendar_sync: "synced" as const }] };
  it("queued until a message exists, then sent", () => {
    expect(whatsappChip({ ...d, messages: [] }, "A1", "booked", "2026-11-08T12:00:00Z").text).toBe("WhatsApp queued");
    expect(whatsappChip(d, "A1", "booked", "2026-11-08T12:00:00Z").text).toBe("WhatsApp sent");
  });
  it("ignores an older message of another change", () => {
    expect(whatsappChip(d, "A1", "booked", "2026-11-08T13:00:00Z").text).toBe("WhatsApp queued");
  });
  it("reports calendar state", () => {
    expect(calendarChip(d, "A1", "booked")?.text).toBe("Calendar synced");
    expect(calendarChip({ ...d, bookings: [] }, "A1", "booked")?.text).toBe("Calendar pending");
  });
});
