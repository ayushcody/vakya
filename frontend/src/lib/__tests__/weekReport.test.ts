import { buildWeek, isOpen, languageLabel, versusLastWeek, whenOf } from "@/lib/weekReport";
import type { CallSummary } from "@/app/(app)/desk/calls/types";
import type { Booking } from "@/components/desk/calls";
import type { Schedule } from "@/lib/schedules";

// Thu 12 Nov 2026, 3 pm in Kolkata.
const NOW = new Date("2026-11-12T09:30:00Z");
const TZ = "Asia/Kolkata";

let n = 0;
function call(when: string, over: Partial<CallSummary> = {}): CallSummary {
  n++;
  return {
    id: `c${n}`,
    agent_id: "a1",
    agent_name: "Clinic desk",
    channel: "phone",
    practice: false,
    when,
    caller: "••••3210",
    duration_s: 120,
    outcome: { key: "answered", label: "Answered", detail: null, request_id: null },
    replies: 3,
    preview: "",
    language: "hi",
    ...over,
  };
}

function booking(createdAt: string, over: Partial<Booking> = {}): Booking {
  return {
    id: "A1", callId: null, name: "Test", phone: null, service: null, reason: null, start: createdAt, timezone: TZ,
    createdAt, status: "booked", calendar: null, agentName: null, practice: false, lines: [], fromCalls: false, source: "phone",
    ...over,
  };
}

const weekdays = { start: "10:00", end: "19:00" };
const clinic: Schedule = {
  id: "s1", agent_id: "a1", name: "OPD", description: null, kind: "slots", accent: "violet", timezone: TZ,
  slot_minutes: 15, buffer_minutes: 0, capacity: 1,
  week: { mon: [weekdays], tue: [weekdays], wed: [weekdays], thu: [weekdays], fri: [weekdays], sat: [weekdays], sun: [] },
  check_in: null, check_out: null, min_notice_minutes: 0, horizon_days: 60, closed_dates: ["2026-11-10"],
};

describe("week report", () => {
  it("reads an instant in the desk's time zone, not the browser's", () => {
    // 20:00 UTC is 1:30 am the next day in Kolkata.
    expect(whenOf("2026-11-11T20:00:00Z", TZ)).toEqual({ day: "2026-11-12", dow: "thu", minute: 90 });
  });

  it("knows open and closed hours, and a closed date", () => {
    expect(isOpen([clinic], "2026-11-12", "thu", 11 * 60)).toBe(true);
    expect(isOpen([clinic], "2026-11-12", "thu", 20 * 60)).toBe(false);
    expect(isOpen([clinic], "2026-11-15", "sun", 11 * 60)).toBe(false);
    expect(isOpen([clinic], "2026-11-10", "tue", 11 * 60)).toBe(false);
    expect(isOpen([], "2026-11-12", "thu", 11 * 60)).toBeNull();
  });

  it("covers today and the six days before it", () => {
    const r = buildWeek({ calls: [], bookings: [], schedules: [], timezone: TZ, now: NOW });
    expect(r.days).toEqual(["2026-11-06", "2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]);
  });

  it("counts only real calls, and splits this week from the one before", () => {
    const r = buildWeek({
      calls: [
        call("2026-11-12T05:00:00Z"), // Thu 10:30 am
        call("2026-11-11T15:00:00Z"), // Wed 8:30 pm, after hours
        call("2026-11-08T06:00:00Z"), // Sun 11:30 am, closed all day
        call("2026-11-12T06:00:00Z", { practice: true }),
        call("2026-11-12T06:00:00Z", { empty: true }),
        call("2026-11-12T06:00:00Z", { channel: "whatsapp" }),
        call("2026-11-03T06:00:00Z"), // the week before
        call("2026-10-20T06:00:00Z"), // older still
      ],
      bookings: [],
      schedules: [clinic],
      timezone: TZ,
      now: NOW,
    });
    expect(r.calls).toBe(3);
    expect(r.previousCalls).toBe(1);
    expect(r.afterHours).toBe(2);
    expect(r.minutes).toBe(6);
  });

  it("does not claim after-hours calls without opening hours", () => {
    const r = buildWeek({ calls: [call("2026-11-11T15:00:00Z")], bookings: [], schedules: [], timezone: TZ, now: NOW });
    expect(r.afterHours).toBeNull();
    expect(r.cells[0][0].open).toBeNull();
  });

  it("places calls on the heatmap and finds the busiest hour", () => {
    const r = buildWeek({
      calls: [call("2026-11-12T05:00:00Z"), call("2026-11-12T05:20:00Z"), call("2026-11-11T05:00:00Z")],
      bookings: [],
      schedules: [clinic],
      timezone: TZ,
      now: NOW,
    });
    expect(r.firstHour).toBe(8);
    expect(r.cells[6][10 - 8]).toEqual({ count: 2, open: true });
    expect(r.cells[6][20 - 8].open).toBe(false);
    expect(r.busiest).toEqual({ day: "2026-11-12", hour: 10, count: 2 });
    expect(r.maxCell).toBe(2);
  });

  it("widens the hours to fit a call in the night", () => {
    const r = buildWeek({ calls: [call("2026-11-11T20:00:00Z")], bookings: [], schedules: [clinic], timezone: TZ, now: NOW });
    expect(r.firstHour).toBe(1);
    expect(r.cells[6][0].count).toBe(1);
  });

  it("lists outcomes in a fixed order and the calls to look at", () => {
    const r = buildWeek({
      calls: [
        call("2026-11-12T05:00:00Z", { outcome: { key: "couldnt_answer", label: "Couldn't answer", detail: null, request_id: null } }),
        call("2026-11-12T06:00:00Z", { outcome: { key: "booked", label: "Booked", detail: null, request_id: null } }),
        call("2026-11-12T07:00:00Z"),
      ],
      bookings: [],
      schedules: [],
      timezone: TZ,
      now: NOW,
    });
    expect(r.outcomes.map((o) => o.key)).toEqual(["booked", "answered", "couldnt_answer"]);
    expect(r.attention.map((c) => c.outcome.key)).toEqual(["couldnt_answer"]);
  });

  it("names languages and keeps the undetected ones last", () => {
    const r = buildWeek({
      calls: [call("2026-11-12T05:00:00Z", { language: null }), call("2026-11-12T05:00:00Z", { language: "mr" }), call("2026-11-12T05:00:00Z", { language: "mr" })],
      bookings: [],
      schedules: [],
      timezone: TZ,
      now: NOW,
    });
    expect(r.languages.map((l) => [l.label, l.count])).toEqual([["Marathi", 2], ["Not detected", 1]]);
    expect(languageLabel("ta")).toBe("Tamil");
  });

  it("counts bookings the desk made, not the ones staff added or cancelled", () => {
    const r = buildWeek({
      calls: [],
      bookings: [
        booking("2026-11-12T05:00:00Z"),
        booking("2026-11-11T05:00:00Z", { source: "missed_call" }),
        booking("2026-11-11T05:00:00Z", { source: "staff" }),
        booking("2026-11-11T05:00:00Z", { status: "cancelled" }),
        booking("2026-11-02T05:00:00Z"),
      ],
      schedules: [],
      timezone: TZ,
      now: NOW,
    });
    expect(r.booked).toBe(2);
    expect(r.previousBooked).toBe(1);
  });

  it("compares with the week before only when there is one", () => {
    expect(versusLastWeek(12, 0)).toBeNull();
    expect(versusLastWeek(12, 8)).toBe("up from 8 the week before");
    expect(versusLastWeek(5, 8)).toBe("down from 8 the week before");
    expect(versusLastWeek(8, 8)).toBe("the same as the week before");
  });
});
