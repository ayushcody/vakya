import { clock, initialLive, languageNote, reduceLive, type LiveEvent } from "../liveCall";
import { guestLine, namesLine, shortName } from "../schedules";
import { focusCss } from "@/components/live/LiveCalendar";

const env = (seq: number, extra: object) =>
  ({ seq, at: "2026-11-05T10:00:00Z", agent_id: "a1", conversation_id: "c1", turn_id: "t1", ...extra }) as unknown as LiveEvent;

const check = [
  env(1, { type: "caller", text: "Is Thursday 6 pm free?", typed: false, language: "en", script: "latin", unsure: false, words: [["Is", "en"]] }),
  env(2, { type: "tool", tool: "check_availability", call_id: "t1:0", phase: "start", status: "checking", schedule_id: "S1", schedule: "Doctor", date: "2026-11-05", time: "18:00", label: "Checking Thu 5 Nov 6 pm" }),
  env(3, { type: "tool", tool: "check_availability", call_id: "t1:0", phase: "end", status: "taken", schedule_id: "S1", schedule: "Doctor", date: "2026-11-05", time: "18:00", available: false, options: [{ date: "2026-11-05", time: "17:30" }, { date: "2026-11-05", time: "18:30" }], label: "Checked Thu 5 Nov 6 pm: taken, offered 5:30 pm or 6:30 pm" }),
];

describe("the live call reducer", () => {
  it("pairs a tool's start and end into one chip and points the calendar at the slot", () => {
    const s0 = reduceLive(initialLive(), check.slice(0, 2));
    expect(s0.lines.map((l) => l.kind)).toEqual(["caller", "tool"]);
    expect(s0.focus).toMatchObject({ scheduleId: "S1", date: "2026-11-05", checking: { time: "18:00", state: "checking" }, offered: [] });
    const s1 = reduceLive(s0, check.slice(2));
    expect(s1.lines).toHaveLength(2);                                   // the end joined the start's chip
    const chip = s1.lines[1] as Extract<(typeof s1.lines)[number], { kind: "tool" }>;
    expect(chip.start?.phase).toBe("start");
    expect(chip.end?.label).toContain("taken, offered 5:30 pm or 6:30 pm");
    expect(s1.focus.checking).toEqual({ time: "18:00", state: "taken" });
    expect(s1.focus.offered.map((o) => o.time)).toEqual(["17:30", "18:30"]);
    expect(s1.lastSeq).toBe(3);
  });

  it("ignores an event it has already seen", () => {
    const once = reduceLive(initialLive(), check);
    expect(reduceLive(once, check)).toEqual(once);
  });

  it("holds the slot while the agent reads it back, then books it and raises the toast", () => {
    const base = reduceLive(initialLive(), check);
    const held = reduceLive(base, [env(4, { type: "tool", tool: "book", call_id: "t2:0", phase: "end", status: "readback", schedule_id: "S1", date: "2026-11-05", time: "17:30", label: "Reading back" }),
      env(5, { type: "agent", index: 0, text: "Thursday 5 November at 5:30 pm. Shall I book it?", audio_ms: 3000, readback: true, disclosure: false, language: "en", words: [] })], 1000);
    expect(held.focus.holding).toEqual({ date: "2026-11-05", time: "17:30" });
    expect(held.focus.offered).toEqual([]);
    const agent = held.lines.at(-1) as Extract<(typeof held.lines)[number], { kind: "agent" }>;
    expect([agent.playFrom, agent.playUntil]).toEqual([1000, 4000]);
    const booked = reduceLive(held, [env(6, { type: "tool", tool: "book", call_id: "t3:0", phase: "end", status: "booked", schedule_id: "S1", date: "2026-11-05", time: "17:30", booking_id: "A12", label: "Booked A12" }),
      env(7, { type: "booking", booking_id: "A12", name: "Priya Sharma", schedule_id: "S1", schedule: "Doctor", date: "2026-11-05", time: "17:30", start: "2026-11-05T17:30", evidence_id: "e1", receipt_id: "e1", evidence_complete: true })], 5000);
    expect(booked.focus.holding).toBeNull();
    expect(booked.focus.booked).toMatchObject({ date: "2026-11-05", time: "17:30", bookingId: "A12" });
    expect(booked.justBooked).toMatchObject({ bookingId: "A12", name: "Priya Sharma", evidenceId: "e1" });
  });

  it("starts the screen again for a new call, so one opened late shows only the latest", () => {
    const old = reduceLive(initialLive(), check);
    const next = reduceLive(old, [{ ...check[0], seq: 10, conversation_id: "c2" } as LiveEvent]);
    expect(next.conversationId).toBe("c2");
    expect(next.lines).toHaveLength(1);
    expect(next.focus.offered).toEqual([]);
    expect(next.lastSeq).toBe(10);
  });

  it("queues sentences so each is marked while it plays", () => {
    const say = (seq: number) => env(seq, { type: "agent", index: seq, text: "Hello there.", audio_ms: 1000, readback: false, disclosure: false, language: "en", words: [] });
    const s = reduceLive(initialLive(), [say(1), say(2)], 100);
    const [a, b] = s.lines as Extract<(typeof s.lines)[number], { kind: "agent" }>[];
    expect([a.playFrom, a.playUntil, b.playFrom, b.playUntil]).toEqual([100, 1100, 1100, 2100]);
  });
});

describe("words, names and times", () => {
  it("says what language was heard and whether it is a guess", () => {
    expect(languageNote("en", "latin", false)).toBe("English");
    expect(languageNote("hi", "devanagari", false)).toBe("Hindi · Devanagari");
    expect(languageNote("mr", "devanagari", true)).toBe("Probably Marathi · Devanagari");
    expect(clock("17:30")).toBe("5:30 pm");
    expect(clock("09:00")).toBe("9 am");
  });

  it("shortens names to a first name and an initial", () => {
    expect(shortName("Sunita Kulkarni")).toBe("Sunita K.");
    expect(shortName("Asha")).toBe("Asha");
    expect(shortName("सुनीता कुलकर्णी")).toBe("सुनीता कु.");
    expect(shortName("राधा शर्मा")).toBe("राधा श.");
    expect(shortName("anil kumar patil")).toBe("anil P.");
    expect(shortName(null)).toBe("A caller");
    const b = (id: string, name: string, nights?: number) => ({ id, name, nights });
    expect(namesLine([b("1", "Sunita Kulkarni"), b("2", "Ravi Menon"), b("3", "Arjun Shah")])).toBe("Sunita K., Ravi M. +1");
    expect(namesLine([b("1", "Sunita Kulkarni")])).toBe("Sunita K.");
    expect(guestLine(b("1", "Arjun Shah", 2))).toBe("Arjun S. · 2 nights");
    expect(guestLine(b("1", "Arjun Shah", 1))).toBe("Arjun S. · 1 night");
  });

  it("lays the call over the calendar: pulse, offered, holding, booked", () => {
    const s = reduceLive(initialLive(), check);
    const css = focusCss(s.focus);
    expect(css).toContain('[data-start^="2026-11-05T18:00"]');
    expect(css).toContain('content:"Taken"');
    expect(css).toContain('[data-start^="2026-11-05T17:30"]');
    expect(css.match(/content:"Offered"/g)).toHaveLength(2);
    expect(css).not.toContain("liveFill");
    const booked = reduceLive(s, [env(9, { type: "booking", booking_id: "A12", name: null, schedule_id: "S1", schedule: "x", date: "2026-11-05", time: "17:30", start: "", evidence_id: null, receipt_id: null, evidence_complete: null })]);
    expect(focusCss(booked.focus)).toContain("liveFill");
    expect(focusCss(booked.focus)).toContain("Booked");
  });
});
