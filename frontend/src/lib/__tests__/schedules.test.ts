import {
  DAY_PRESETS,
  NIGHT_RANGE,
  activePreset,
  addDays,
  addMonths,
  applyParsedHours,
  buildGrid,
  defaultDraft,
  dayList,
  draftToInput,
  draftWeek,
  dragSelection,
  emptyWeek,
  fromMin,
  hoursInWords,
  kindLine,
  monthGrid,
  nextFreeWords,
  nowIn,
  offsetOf,
  parseTimeInput,
  plainError,
  rangeWords,
  scheduleSentence,
  scheduleToDraft,
  slotLabel,
  timeWords,
  toMin,
  toggleDay,
  validateDraft,
  validateRanges,
  weekDays,
  weekFor,
  weekRangeLabel,
  weekStart,
  withBlock,
  withoutBlock,
  type CellState,
  type Schedule,
  type Slot,
  type SlotDay,
  type SlotsResponse,
} from "@/lib/schedules";
import { ApiError } from "@/lib/apiBase";

const clinic = weekFor(["mon", "tue", "wed", "thu", "fri", "sat"], [{ start: "10:00", end: "13:00" }, { start: "17:00", end: "21:00" }]);

describe("times in words", () => {
  it("reads and writes times of day", () => {
    expect(toMin("10:30")).toBe(630);
    expect(toMin("24:00")).toBe(1440);
    expect(toMin("25:00")).toBeNaN();
    expect(toMin("9am")).toBeNaN();
    expect(fromMin(1440)).toBe("24:00");
    expect(fromMin(65)).toBe("01:05");
  });
  it("says them like a person", () => {
    expect(timeWords("10:00")).toBe("10 am");
    expect(timeWords("17:30")).toBe("5:30 pm");
    expect(timeWords("12:00")).toBe("noon");
    expect(timeWords("24:00")).toBe("midnight");
    expect(timeWords("00:00")).toBe("midnight");
  });
  it("squeezes a range when both ends share am or pm", () => {
    expect(rangeWords({ start: "10:00", end: "13:00" })).toBe("10 am to 1 pm");
    expect(rangeWords({ start: "17:00", end: "21:00" })).toBe("5 to 9 pm");
    expect(rangeWords({ start: "09:30", end: "11:45" })).toBe("9:30 to 11:45 am");
    expect(rangeWords({ start: "00:00", end: "24:00" })).toBe("all day");
    expect(rangeWords({ start: "12:00", end: "15:00" })).toBe("noon to 3 pm");
  });
});

describe("parseTimeInput", () => {
  it("understands typed times", () => {
    expect(parseTimeInput("5pm")).toBe("17:00");
    expect(parseTimeInput("5:30 pm")).toBe("17:30");
    expect(parseTimeInput("17:30")).toBe("17:30");
    expect(parseTimeInput("1730")).toBe("17:30");
    expect(parseTimeInput("9.15am")).toBe("09:15");
    expect(parseTimeInput("noon")).toBe("12:00");
    expect(parseTimeInput("12am")).toBe("00:00");
    expect(parseTimeInput("12 pm")).toBe("12:00");
  });
  it("reads a bare hour so an end comes after its start", () => {
    expect(parseTimeInput("5", { after: 9 * 60 })).toBe("17:00");
    expect(parseTimeInput("10", { after: 9 * 60 })).toBe("10:00");
    expect(parseTimeInput("midnight", { after: 600 })).toBe("24:00");
    expect(parseTimeInput("9")).toBe("09:00");
    expect(parseTimeInput("5")).toBe("17:00");
  });
  it("refuses what is not a time", () => {
    expect(parseTimeInput("")).toBeNull();
    expect(parseTimeInput("soon")).toBeNull();
    expect(parseTimeInput("25:00")).toBeNull();
    expect(parseTimeInput("13pm")).toBeNull();
    expect(parseTimeInput("9:75")).toBeNull();
  });
});

describe("hours in plain words", () => {
  it("groups days that share hours", () => {
    expect(hoursInWords(clinic, "short")).toBe("Mon to Sat, 10 am to 1 pm and 5 to 9 pm");
    expect(hoursInWords(clinic, "long")).toBe("Monday to Saturday, 10 am to 1 pm and 5 to 9 pm");
  });
  it("separates days with different hours", () => {
    const w = { ...clinic, sat: [{ start: "10:00", end: "14:00" }] };
    expect(hoursInWords(w, "short")).toBe("Mon to Fri, 10 am to 1 pm and 5 to 9 pm; Sat, 10 am to 2 pm");
  });
  it("says every day, and closed", () => {
    expect(hoursInWords(weekFor(DAY_PRESETS[0].days, [{ start: "09:00", end: "17:00" }]))).toBe("Every day, 9 am to 5 pm");
    expect(hoursInWords(emptyWeek())).toBe("Closed every day");
  });
  it("lists days as runs", () => {
    expect(dayList(["mon", "wed", "fri"])).toBe("Mon, Wed and Fri");
    expect(dayList(["sat", "sun"], "long")).toBe("Saturday and Sunday");
    expect(dayList(["tue", "wed", "thu", "sat", "sun"])).toBe("Tue to Thu, Sat and Sun");
  });
  it("describes the kind of schedule", () => {
    expect(kindLine({ kind: "slots", slot_minutes: 15, capacity: 1 })).toBe("Every 15 min");
    expect(kindLine({ kind: "slots", slot_minutes: 30, capacity: 3 })).toBe("Every 30 min, 3 at a time");
    expect(kindLine({ kind: "nights", slot_minutes: 60, capacity: 10 })).toBe("Nightly, 10 rooms");
  });
});

describe("day presets", () => {
  const byId = (id: string) => DAY_PRESETS.find((p) => p.id === id)!;
  it("has the five one-tap choices", () => {
    expect(DAY_PRESETS.map((p) => p.label)).toEqual(["All days", "Weekdays", "Mon to Sat", "Weekends", "Closed"]);
    expect(byId("weekdays").days).toEqual(["mon", "tue", "wed", "thu", "fri"]);
    expect(byId("weekends").days).toEqual(["sat", "sun"]);
    expect(byId("closed").days).toEqual([]);
  });
  it("tells which one is on", () => {
    expect(activePreset(["mon", "tue", "wed", "thu", "fri", "sat"])?.id).toBe("monsat");
    expect(activePreset([])?.id).toBe("closed");
    expect(activePreset(["mon", "wed"])).toBeUndefined();
  });
  it("toggles a day and keeps the week order", () => {
    expect(toggleDay(["mon", "wed"], "tue")).toEqual(["mon", "tue", "wed"]);
    expect(toggleDay(["mon", "tue"], "mon")).toEqual(["tue"]);
  });
});

describe("validation in plain words", () => {
  it("catches an end before the start, naming the session", () => {
    const issues = validateRanges([{ start: "10:00", end: "13:00" }, { start: "21:00", end: "17:00" }]);
    expect(issues).toHaveLength(1);
    expect(issues[0].message).toBe("The evening session ends before it starts.");
    expect(issues[0].index).toBe(1);
  });
  it("calls a lone range the opening hours", () => {
    expect(validateRanges([{ start: "17:00", end: "09:00" }])[0].message).toBe("The opening hours ends before it starts.");
  });
  it("catches sessions that overlap, but lets them touch", () => {
    expect(validateRanges([{ start: "09:00", end: "13:00" }, { start: "12:00", end: "17:00" }])[0].message).toMatch(/overlap/);
    expect(validateRanges([{ start: "09:00", end: "13:00" }, { start: "13:00", end: "17:00" }])).toEqual([]);
  });
  it("asks for a name, and for hours that fit a slot", () => {
    const d = { ...defaultDraft("a1"), name: "" };
    expect(validateDraft(d).map((i) => i.field)).toContain("name");
    const tiny = { ...defaultDraft("a1"), name: "Rooms", shared: [{ start: "10:00", end: "10:10" }], slot_minutes: 15 };
    expect(validateDraft(tiny).some((i) => i.field === "slots" && /shorter than a 15-minute slot/.test(i.message))).toBe(true);
  });
  it("checks each day when hours differ", () => {
    const d = { ...defaultDraft("a1"), name: "X", sameHours: false, openDays: ["mon" as const], perDay: { ...weekFor(["mon"], []), mon: [] } };
    expect(validateDraft(d)[0].message).toBe("Add opening hours for Monday, or close it.");
  });
  it("wants check-out earlier in the day than check-in for nights", () => {
    const d = { ...defaultDraft("a1"), name: "Rooms", kind: "nights" as const, check_in: "11:00", check_out: "12:00" };
    expect(validateDraft(d).some((i) => i.field === "stay")).toBe(true);
    expect(validateDraft({ ...d, check_in: "12:00", check_out: "11:00" })).toEqual([]);
  });
  it("passes a good draft", () => {
    expect(validateDraft({ ...defaultDraft("a1"), name: "Doctor consultations" })).toEqual([]);
  });
});

describe("drafts", () => {
  it("builds the week from days and shared hours, sorted", () => {
    const d = { ...defaultDraft("a1"), openDays: ["mon" as const, "sun" as const], shared: [{ start: "17:00", end: "20:00" }, { start: "10:00", end: "13:00" }] };
    const w = draftWeek(d);
    expect(w.mon.map((r) => r.start)).toEqual(["10:00", "17:00"]);
    expect(w.tue).toEqual([]);
    expect(w.sun).toHaveLength(2);
  });
  it("gives nights one whole-day range on each open night", () => {
    const d = { ...defaultDraft("a1"), kind: "nights" as const, openDays: ["fri" as const, "sat" as const] };
    expect(draftWeek(d).fri).toEqual([NIGHT_RANGE]);
    expect(draftWeek(d).mon).toEqual([]);
  });
  it("round-trips a stored schedule through the editor", () => {
    const s: Schedule = {
      id: "S1", agent_id: "a1", name: "Consults", description: null, kind: "slots", accent: "teal", timezone: "Asia/Kolkata",
      slot_minutes: 20, buffer_minutes: 5, capacity: 2, week: { ...clinic, sat: [{ start: "10:00", end: "14:00" }] },
      check_in: null, check_out: null, min_notice_minutes: 60, horizon_days: 30, closed_dates: ["2026-11-12"],
    };
    const d = scheduleToDraft(s);
    expect(d.sameHours).toBe(false);
    expect(d.openDays).toEqual(["mon", "tue", "wed", "thu", "fri", "sat"]);
    const input = draftToInput(d);
    expect(input.week).toEqual(s.week);
    expect(input).toMatchObject({ name: "Consults", slot_minutes: 20, buffer_minutes: 5, capacity: 2, accent: "teal", agent_id: "a1", check_in: null });
    expect(input).not.toHaveProperty("timezone");
    expect(input).not.toHaveProperty("id");
  });
  it("sends check-in and check-out only for nights", () => {
    const input = draftToInput({ ...defaultDraft("a1"), name: "Rooms", kind: "nights", capacity: 10 });
    expect(input).toMatchObject({ kind: "nights", check_in: "12:00", check_out: "11:00", capacity: 10 });
  });
  it("fills the form from parsed hours and reports what moved", () => {
    const base = { ...defaultDraft("a1"), name: "X" };
    const parsed = { week: weekFor(["mon", "tue", "wed", "thu", "fri", "sat"], [{ start: "09:00", end: "13:00" }]), slot_minutes: 30, closed_dates: ["2026-11-12"], notes: [], confident: true };
    const { draft, changed } = applyParsedHours(base, parsed);
    expect(draft.shared).toEqual([{ start: "09:00", end: "13:00" }]);
    expect(draft.slot_minutes).toBe(30);
    expect(draft.closed_dates).toEqual(["2026-11-12"]);
    expect(changed).toEqual(expect.arrayContaining(["hours", "slots", "holidays"]));
    expect(changed).not.toContain("days");
  });
  it("says the sentence callers hear", () => {
    expect(scheduleSentence({ ...defaultDraft("a1"), shared: [{ start: "10:00", end: "13:00" }, { start: "17:00", end: "21:00" }] })).toBe(
      "Open Monday to Saturday, 10 am to 1 pm and 5 to 9 pm. 15-minute slots, one at a time."
    );
    expect(scheduleSentence({ ...defaultDraft("a1"), openDays: [] })).toMatch(/^Closed every day/);
    expect(scheduleSentence({ ...defaultDraft("a1"), kind: "nights", capacity: 10, openDays: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] })).toBe(
      "Nightly stays, 10 rooms, bookable every night. Check-in from noon, check-out by 11 am."
    );
    expect(scheduleSentence({ ...defaultDraft("a1"), capacity: 3, buffer_minutes: 5, slot_minutes: 30 })).toMatch(/30-minute slots, up to 3 at the same time, with 5 minutes between bookings\.$/);
  });
});

describe("dates", () => {
  it("works in whole days without drifting", () => {
    expect(addDays("2026-11-03", 7)).toBe("2026-11-10");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(weekStart("2026-11-05")).toBe("2026-11-02"); // a Thursday
    expect(weekStart("2026-11-08")).toBe("2026-11-02"); // the Sunday belongs to the week before
    expect(weekDays("2026-11-05")).toHaveLength(7);
    expect(addMonths("2026-11-20", 2)).toBe("2027-01-01");
  });
  it("lays out a month as 42 days from a Monday", () => {
    const g = monthGrid("2026-11-15");
    expect(g).toHaveLength(42);
    expect(g[0]).toBe("2026-10-26");
    expect(g.includes("2026-11-01")).toBe(true);
  });
  it("labels a week", () => {
    expect(weekRangeLabel("2026-11-05")).toMatch(/^2 to 8 Nov 2026$/);
  });
  it("finds the date and minutes in a zone", () => {
    const t = Date.UTC(2026, 10, 3, 18, 30); // 18:30 UTC is midnight at +5:30
    expect(nowIn("Asia/Kolkata", t)).toEqual({ date: "2026-11-04", minutes: 0 });
  });
  it("says when the next free time is", () => {
    expect(nextFreeWords("2026-11-03T18:00:00+05:30", "2026-11-03")).toBe("Today, 6 pm");
    expect(nextFreeWords("2026-11-04T09:30:00+05:30", "2026-11-03")).toBe("Tomorrow, 9:30 am");
    expect(nextFreeWords("2026-11-09T09:30:00+05:30", "2026-11-03")).toMatch(/9:30 am$/);
    expect(nextFreeWords(null, "2026-11-03")).toBe("Nothing free soon");
  });
});

function slot(date: string, hhmm: string, mins: number, over: Partial<Slot> = {}): Slot {
  const end = fromMin(toMin(hhmm) + mins);
  return { start: `${date}T${hhmm}:00+05:30`, end: `${date}T${end}:00+05:30`, status: "free", remaining: 1, bookings: [], block_id: null, ...over };
}
const day = (date: string, starts: string[], mins = 15): SlotDay => ({ date, open: starts.length > 0, slots: starts.map((s) => slot(date, s, mins)) });

describe("the calendar grid", () => {
  it("makes a row for each interval and collapses a long closed stretch", () => {
    const morning = ["10:00", "10:15", "10:30", "10:45"];
    const evening = ["17:00", "17:15"];
    const g = buildGrid([day("2026-11-02", [...morning, ...evening])], 15);
    expect(g.step).toBe(15);
    expect(g.rows.filter((r) => r.kind === "slot").map((r) => (r.kind === "slot" ? r.time : ""))).toEqual([...morning, ...evening]);
    const gap = g.rows.find((r) => r.kind === "gap");
    expect(gap).toMatchObject({ kind: "gap", from: "11:00", to: "17:00" });
  });
  it("keeps short breaks as ordinary closed rows", () => {
    const g = buildGrid([day("2026-11-02", ["10:00", "10:15", "10:45", "11:00"])], 15);
    expect(g.rows.every((r) => r.kind === "slot")).toBe(true);
    expect(g.rows).toHaveLength(5);
  });
  it("uses the union of the days in view and handles an empty week", () => {
    const g = buildGrid([day("2026-11-02", ["10:00"]), day("2026-11-03", ["09:45", "10:00"])], 15);
    expect(g.rows.map((r) => (r.kind === "slot" ? r.time : "gap"))).toEqual(["09:45", "10:00"]);
    expect(buildGrid([], 15).rows).toEqual([]);
  });
  it("finds the height of a time of day", () => {
    const g = buildGrid([day("2026-11-02", ["10:00", "10:15", "17:00"])], 15);
    expect(offsetOf(g.rows, 10 * 60, g.step, 30, 22)).toBe(0);
    expect(offsetOf(g.rows, 10 * 60 + 7.5, g.step, 30, 22)).toBe(15);
    expect(offsetOf(g.rows, 17 * 60, g.step, 30, 22)).toBe(30 * 2 + 22);
    expect(offsetOf(g.rows, 9 * 60, g.step, 30, 22)).toBeNull();
  });
});

describe("press and drag to mark busy", () => {
  const f: CellState = "free";
  it("selects from the press to the pointer, in either direction", () => {
    const col: CellState[] = [f, f, f, f, f];
    expect(dragSelection(col, 1, 3)).toEqual({ start: 1, end: 3 });
    expect(dragSelection(col, 3, 1)).toEqual({ start: 1, end: 3 });
    expect(dragSelection(col, 2, 2)).toEqual({ start: 2, end: 2 });
  });
  it("stops before anything that is not free", () => {
    const col: CellState[] = [f, f, "full", f, "blocked", f];
    expect(dragSelection(col, 0, 5)).toEqual({ start: 0, end: 1 });
    expect(dragSelection(col, 3, 5)).toEqual({ start: 3, end: 3 });
    expect(dragSelection(col, 5, 0)).toEqual({ start: 5, end: 5 });
    expect(dragSelection(["closed", f, f, "past"], 1, 3)).toEqual({ start: 1, end: 2 });
  });
  it("does nothing when the press began on a busy or closed cell", () => {
    expect(dragSelection(["full", f], 0, 1)).toBeNull();
    expect(dragSelection(["closed", f], 0, 1)).toBeNull();
    expect(dragSelection([f], 4, 5)).toBeNull();
  });
  it("clamps a pointer that runs past the ends", () => {
    expect(dragSelection([f, f, f], 1, 99)).toEqual({ start: 1, end: 2 });
    expect(dragSelection([f, f, f], 1, -4)).toEqual({ start: 0, end: 1 });
  });
});

describe("optimistic changes", () => {
  const data: SlotsResponse = {
    timezone: "Asia/Kolkata",
    kind: "slots",
    days: [{ date: "2026-11-05", open: true, slots: [slot("2026-11-05", "10:00", 15), slot("2026-11-05", "10:15", 15), slot("2026-11-05", "10:30", 15, { status: "full", remaining: 0 })] }],
  };
  it("shows a range as busy and leaves booked slots alone", () => {
    const next = withBlock(data, "2026-11-05T10:00:00+05:30", "2026-11-05T10:45:00+05:30", "K9");
    expect(next.days[0].slots.map((s) => s.status)).toEqual(["blocked", "blocked", "full"]);
    expect(next.days[0].slots[0].block_id).toBe("K9");
    expect(data.days[0].slots[0].status).toBe("free"); // the original is untouched, so a rollback is exact
  });
  it("frees a block again", () => {
    const blocked = withBlock(data, "2026-11-05T10:00:00+05:30", "2026-11-05T10:30:00+05:30", "K9");
    const back = withoutBlock(blocked, "K9", 3);
    expect(back.days[0].slots.map((s) => s.status)).toEqual(["free", "free", "full"]);
    expect(back.days[0].slots[0].remaining).toBe(3);
  });
  it("labels each state with words, not only colour", () => {
    expect(slotLabel(slot("d", "10:00", 15), 1)).toBe("Free");
    expect(slotLabel(slot("d", "10:00", 15, { status: "booked", remaining: 2 }), 3)).toBe("2 of 3 left");
    expect(slotLabel(slot("d", "10:00", 15, { status: "full", remaining: 0, bookings: [{ id: "A1", name: "Priya" }] }), 1)).toBe("Priya");
    expect(slotLabel(slot("d", "10:00", 15, { status: "full", remaining: 0 }), 4)).toBe("Full");
    expect(slotLabel(slot("d", "10:00", 15, { status: "blocked" }), 1)).toBe("Busy");
  });
});

describe("errors for people", () => {
  it("never shows a code or JSON", () => {
    expect(plainError(new ApiError("Not Found", 404, "NOT_FOUND"), "x")).toMatch(/could not be found/);
    expect(plainError(new ApiError("network", 0, "network_error"), "x")).toMatch(/Could not reach the server/);
    expect(plainError(new ApiError('{"detail":"x"}', 500, "http_500"), "Fallback")).toBe("Fallback");
    expect(plainError(new Error("HAS_BOOKINGS"), "Fallback")).toBe("Fallback");
    expect(plainError(new Error("The schedule name is too long."), "Fallback")).toBe("The schedule name is too long.");
    expect(plainError("weird", "Fallback")).toBe("Fallback");
  });
});
