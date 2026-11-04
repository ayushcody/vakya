import {
  dayOfInstant, deskTimezone, formatClock, formatDate, formatDateTime, formatDay, formatDayLong, formatDayRange, formatDayTime, formatMonthYear, formatTime,
} from "../formatDate";

const NOW = new Date("2026-11-04T09:00:00Z");

describe("formatDate: one way to write a day and a time", () => {
  it("writes a clock time in twelve hours with a lower-case am or pm", () => {
    expect(formatClock("19:00")).toBe("7 pm");
    expect(formatClock("19:30")).toBe("7:30 pm");
    expect(formatClock("09:05")).toBe("9:05 am");
    expect(formatClock("00:00")).toBe("12 am");
    expect(formatClock("12:00")).toBe("12 pm");
    expect(formatClock("12:45")).toBe("12:45 pm");
    expect(formatClock("24:00")).toBeNull();
    expect(formatClock("soon")).toBeNull();
    expect(formatClock(null)).toBeNull();
  });

  it("writes a day as weekday, day, month", () => {
    expect(formatDay("2026-11-12")).toBe("Thu 12 Nov");
    expect(formatDay("2026-09-01")).toBe("Tue 1 Sep");
    expect(formatDay("2026-11-12", 2026)).toBe("Thu 12 Nov");
    expect(formatDay("2027-01-04", 2026)).toBe("Mon 4 Jan 2027");
    expect(formatDay("2026-02-30")).toBeNull();
    expect(formatDay("")).toBeNull();
  });

  it("joins a day and a time with a comma", () => {
    expect(formatDayTime("2026-11-12", "19:00")).toBe("Thu 12 Nov, 7 pm");
    expect(formatDayTime("2026-11-12", "07:30")).toBe("Thu 12 Nov, 7:30 am");
    expect(formatDayTime("2026-11-12", null)).toBe("Thu 12 Nov");
    expect(formatDayTime(null, "19:00")).toBe("7 pm");
    expect(formatDayTime(null, null)).toBeNull();
  });

  it("shows an instant in the desk's time zone, not the browser's", () => {
    expect(formatDateTime("2026-11-12T13:30:00Z", "Asia/Kolkata", NOW)).toBe("Thu 12 Nov, 7 pm");
    expect(formatDateTime("2026-11-12T13:30:00Z", undefined, NOW)).toBe("Thu 12 Nov, 7 pm");        // Asia/Kolkata by default
    expect(formatDateTime("2026-11-12T19:00:00+05:30", "Asia/Kolkata", NOW)).toBe("Thu 12 Nov, 7 pm");
    expect(formatDateTime("2026-11-12T13:30:00Z", "America/New_York", NOW)).toBe("Thu 12 Nov, 8:30 am");
    expect(formatTime("2026-11-12T13:42:00Z")).toBe("7:12 pm");
    expect(formatDate("2026-11-12T20:30:00Z", "Asia/Kolkata", NOW)).toBe("Fri 13 Nov");               // past midnight there
    expect(formatDate("2027-01-04T05:00:00Z", "Asia/Kolkata", NOW)).toBe("Mon 4 Jan 2027");
  });

  it("is quiet about what it cannot read", () => {
    expect(formatDateTime("not a date")).toBe("");
    expect(formatDateTime(null)).toBe("");
    expect(formatTime(undefined)).toBe("");
    expect(formatDate("")).toBe("");
    expect(deskTimezone("Not/AZone")).toBe("Asia/Kolkata");
    expect(deskTimezone(null)).toBe("Asia/Kolkata");
  });

  it("gives the desk's calendar day of an instant", () => {
    expect(dayOfInstant("2026-11-12T20:30:00Z", "Asia/Kolkata")).toBe("2026-11-13");
    expect(dayOfInstant("2026-11-12T20:30:00Z", "UTC")).toBe("2026-11-12");
    expect(dayOfInstant("x")).toBeNull();
  });
});

describe("formatDate: the long forms the schedule screens use", () => {
  it("writes a long day, a month and a week range without commas", () => {
    expect(formatDayLong("2026-11-12")).toBe("Thursday 12 November");
    expect(formatMonthYear("2026-11-12")).toBe("November 2026");
    expect(formatDayRange("2026-11-09", "2026-11-15")).toBe("9 to 15 Nov 2026");
    expect(formatDayRange("2026-10-26", "2026-11-01")).toBe("26 Oct to 1 Nov 2026");
    expect(formatDayLong("not a day")).toBe("not a day");
  });
});
