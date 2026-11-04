import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { BookingDrawer } from "@/components/schedules/BookingDrawer";
import { BookingList } from "@/components/desk/BookingList";
import { NightsCalendar } from "@/components/schedules/NightsCalendar";
import { WeekCalendar } from "@/components/schedules/WeekCalendar";
import { SlotPopover } from "@/components/schedules/SlotPopover";
import { BookingProof } from "@/components/verify/BookingProof";
import type { BookingEvidence, EvidenceItem } from "@/components/verify/types";
import type { Booking } from "@/components/desk/calls";
import { api } from "@/lib/api";
import { nowIn, weekFor, type Schedule, type Slot, type SlotsResponse } from "@/lib/schedules";

jest.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(), usePathname: () => "/", useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn(), blob: jest.fn() },
  ApiError: jest.requireActual("@/lib/apiBase").ApiError,
  API_URL: "http://api.test",
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const get = api.get as jest.Mock;
const post = api.post as jest.Mock;
const blob = api.blob as jest.Mock;
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

const item = (over: Partial<EvidenceItem>): EvidenceItem => ({
  id: "i1", role: "field", field: "name", label: "Name", speaker: "caller", turn_id: "t2", at: null, transcript: "5:30 then, I am Priya Sharma, 98765 43210",
  language: "en", basis: "heard", typed: false, transcript_sha256: "a".repeat(64), audio_sha256: "b".repeat(64), item_sha256: "c".repeat(64),
  has_audio: true, audio_available: true, duration_s: 3.2, sample_rate: 16000, reply_receipt_id: null, ...over,
});
const evidence = (over: Partial<BookingEvidence> = {}): BookingEvidence => ({
  evidence_id: "e1", receipt_id: "e1", booking_id: "A12", public: false, created_at: "2026-11-05T10:00:00Z", complete: true, deleted: false, retention_days: 180,
  manifest_sha256: "d".repeat(64), verified: true, verification: { receipt_ok: true, manifest_ok: true, reason: null },
  items: [
    item({}),
    item({ id: "i2", field: "phone", label: "Phone number" }),
    item({ id: "i3", field: "date", label: "Date", turn_id: null, transcript: "", has_audio: false, audio_available: false, audio_sha256: null, basis: null }),
    item({ id: "i4", role: "readback", field: null, label: null, speaker: "agent", turn_id: "t2", transcript: "Thursday 5 November at 5:30 pm, for Priya Sharma. Shall I book it?", basis: null }),
    item({ id: "i5", role: "confirm", field: null, label: null, turn_id: "t3", transcript: "yes please", basis: null }),
  ],
  ...over,
});

const booking: Booking = {
  id: "A12", callId: "c1", name: "Priya Sharma", phone: "••••3210", phoneMasked: "••••3210", service: null, reason: null, start: "2026-11-05T17:30:00+05:30",
  timezone: "Asia/Kolkata", createdAt: "2026-11-05T10:00:00+05:30", status: "booked", calendar: "not_configured", agentName: "Sunrise Clinic", agentId: "a1",
  scheduleId: "S1", scheduleName: "Doctor consultations", end: null, nights: null, source: "phone", callerRef: "ref1", callerBookingsCount: 3, language: "hi",
  receiptId: "e1", hasEvidence: true, practice: false, lines: [], fromCalls: false,
};
const others = [
  { ...booking, id: "A9", name: "Ravi Menon" },
  { ...booking, id: "A10", name: "Priya Sharma", start: "2026-11-06T10:00:00+05:30" },
];
const apiBookings = (rows: Booking[]) => ({
  bookings: rows.map((b) => ({ id: b.id, name: b.name, phone: b.phoneMasked, phone_masked: b.phoneMasked, service: null, specialty: null, reason: null, start: b.start, timezone: "Asia/Kolkata", status: b.status, conversation_id: b.callId, receipt_id: b.receiptId, agent_id: "a1", created_at: b.createdAt, calendar_sync: "not_configured", schedule_id: "S1", schedule_name: "Doctor consultations", source: b.source, caller_ref: b.callerRef, caller_bookings_count: 3, has_evidence: true })),
  timezone: "Asia/Kolkata",
});

beforeEach(() => {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.includes("/evidence")) return evidence();
    if (path.startsWith("/api/bookings")) return apiBookings([booking, ...others]);
    throw new Error(`unexpected GET ${path}`);
  });
  post.mockReset().mockResolvedValue({});
  blob.mockReset().mockResolvedValue(new Blob(["x"], { type: "audio/wav" }));
});

describe("the booking drawer", () => {
  it("leads with who the booking is for, then the proof, then the actions", async () => {
    view(<BookingDrawer booking={booking} onClose={jest.fn()} />);
    const dialog = await screen.findByRole("dialog", { name: /Booking A12 for Priya Sharma/ });
    expect(within(dialog).getByRole("heading", { name: "Priya Sharma" })).toBeInTheDocument();
    expect(within(dialog).getByText("••••3210")).toBeInTheDocument();
    expect(within(dialog).getByText("Hindi")).toBeInTheDocument();
    expect(within(dialog).getByText("Returning caller · 3rd booking")).toBeInTheDocument();
    expect(within(dialog).getByText("Phone call")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Open the transcript" })).toHaveAttribute("href", "/desk/calls?call=c1");
    expect(await within(dialog).findByText("Also booked on this number: Ravi Menon")).toBeInTheDocument();
    expect(await within(dialog).findByText("Receipt verified")).toBeInTheDocument();
    // asked, said, read back, confirmed
    for (const step of ["Asked", "Said", "Read back", "Confirmed"]) expect(within(dialog).getByRole("heading", { name: step })).toBeInTheDocument();
    expect(within(dialog).getAllByText(/“5:30 then, I am Priya Sharma/)).toHaveLength(1);       // one utterance gave name and phone
    expect(within(dialog).getByText(/Name, Phone number/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Not found in what the caller said: date/)).toBeInTheDocument();
    expect(within(dialog).getAllByRole("button", { name: /^Play/ })).toHaveLength(3);          // the said clip, the read-back, the yes
    expect(within(dialog).getByRole("link", { name: "Open the caller" })).toHaveAttribute("href", "/desk/callers?caller=ref1");
    expect(within(dialog).getByRole("button", { name: "Cancel booking" })).toBeInTheDocument();
    const order = Array.from(dialog.querySelectorAll("h2, h3, h4, footer")).map((e) => e.textContent?.replace(/\s+/g, " ").slice(0, 24));
    expect(order[0]).toBe("Priya Sharma");
    expect(order.indexOf("How it was booked")).toBeGreaterThan(0);
  });

  it("writes the time the Indian English way, in the booking's time zone", async () => {
    view(<BookingDrawer booking={booking} onClose={jest.fn()} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Thu 5 Nov, 5:30 pm")).toBeInTheDocument();       // not "Thu, Nov 5, 5:30 PM"
    expect(dialog).not.toHaveTextContent(/PM|AM/);
  });

  it("keeps the hashes behind a Show seals toggle so the timeline reads cleanly", async () => {
    view(<BookingDrawer booking={booking} onClose={jest.fn()} />);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Receipt verified")).toBeInTheDocument();
    expect(dialog.textContent).not.toMatch(/words aaaaaaaa|seal dddddddd/);
    const toggle = within(dialog).getByRole("button", { name: "Show seals" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(within(dialog).getByRole("button", { name: "Hide seals" })).toHaveAttribute("aria-pressed", "true");
    expect(within(dialog).getAllByText(/words aaaaaaaa…aaaaaa/).length).toBeGreaterThan(0);
    expect(within(dialog).getByText(/seal dddddddd/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Hide seals" }));
    expect(dialog.textContent).not.toMatch(/words aaaaaaaa|seal dddddddd/);
  });

  it("plays a recording through the signed-in fetch", async () => {
    view(<BookingDrawer booking={booking} onClose={jest.fn()} />);
    const play = (await screen.findAllByRole("button", { name: /^Play confirm|^Play caller/ }))[0];
    window.URL.createObjectURL = jest.fn(() => "blob:x");
    const audio = jest.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue();
    fireEvent.click(play);
    await waitFor(() => expect(blob).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/bookings\/A12\/evidence\/audio\/i\d$/)));
    await waitFor(() => expect(audio).toHaveBeenCalled());
  });

  it("makes the proof public on request and says what that means", async () => {
    view(<BookingDrawer booking={booking} onClose={jest.fn()} />);
    expect(await screen.findByText("Private proof")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make public" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/bookings/A12/evidence/public", { public: true }));
  });

  it("says so when a proof does not verify or there is none", async () => {
    get.mockImplementation(async (path: string) => (path.includes("/evidence") ? evidence({ verified: false, verification: { receipt_ok: true, manifest_ok: false, reason: "tampered" } }) : apiBookings([booking])));
    const { unmount } = view(<BookingDrawer booking={booking} onClose={jest.fn()} />);
    expect(await screen.findByText("This proof does not verify")).toBeInTheDocument();
    expect(screen.getByText(/changed after it was sealed/)).toBeInTheDocument();
    unmount();
    view(<BookingDrawer booking={{ ...booking, hasEvidence: false, source: "staff", callerRef: null }} onClose={jest.fn()} />);
    expect(await screen.findByText(/Staff made this booking from the calendar/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open the caller" })).toBeNull();
  });

  it("opens from a row in the list, name first", async () => {
    view(<BookingList bookings={[booking]} emptyTitle="" emptyText="" />);
    const open = screen.getByRole("button", { name: "Open booking A12 for Priya Sharma" });
    expect(open).toHaveTextContent("Priya Sharma");
    expect(screen.getByText("Proof kept")).toBeInTheDocument();
    fireEvent.click(open);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("the public proof page", () => {
  const fetchMock = jest.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    (global as unknown as { fetch: jest.Mock }).fetch = fetchMock;
  });
  const serve = (e: BookingEvidence) =>
    fetchMock.mockImplementation(async (url: string) => {
      const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
      if (url.endsWith("/booking")) return ok(e);
      return ok({ receipt_id: "e1", kind: "booking", created_at: "2026-11-05T10:00:00Z", signature_valid: true, engine: null, voice_id: null, voice_name: null, consent_id: null, consent_verified: null });
    });

  it("shows redacted words, hashes and no play buttons by default", async () => {
    const redacted = evidence({
      items: evidence().items.map((i) => ({ ...i, audio_available: false, transcript: i.role === "field" && i.field === "name" ? "(the caller said their name)" : i.role === "readback" ? "Thursday 5 November at 5:30 pm, for ▒▒▒ ▒▒▒. Shall I book it?" : i.transcript === "" ? "" : "(the caller said their phone number)" })),
    });
    serve(redacted);
    render(<BookingProof receiptId="e1" />);
    expect(await screen.findByText("Receipt verified")).toBeInTheDocument();
    expect(screen.getByText("How booking A12 was made")).toBeInTheDocument();
    expect(screen.getByText(/has not made the words and recordings public/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Play/ })).toBeNull();
    expect(screen.queryByText(/Priya/)).toBeNull();
    expect(screen.getAllByText(/Recording kept, hidden from the public/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/words aaaaaaaa…aaaaaa/).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/api\/receipts\/e1\/booking$/));   // a plain GET: no sign-in header
  });

  it("offers play buttons for the agent's and the caller's audio once the owner made it public", async () => {
    serve(evidence({ public: true }));
    render(<BookingProof receiptId="e1" />);
    expect(await screen.findByText(/The owner made the words and recordings public/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Play/ })).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Play front desk recording" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Play caller recording" })).toHaveLength(2);
  });

  it("says when a receipt covers no booking, and when the proof was tampered with", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });
    const { unmount } = render(<BookingProof receiptId="zzz" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/No booking proof was found/);
    unmount();
    serve(evidence({ verified: false, verification: { receipt_ok: true, manifest_ok: false, reason: "tampered" } }));
    render(<BookingProof receiptId="e1" />);
    expect(await screen.findByText("This proof does not verify")).toBeInTheDocument();
    expect(screen.queryByText("Receipt verified")).toBeNull();
  });
});

describe("who is in a slot", () => {
  const schedule: Schedule = {
    id: "S1", agent_id: "a1", name: "Doctor consultations", description: null, kind: "slots", accent: "violet", timezone: "Asia/Kolkata",
    slot_minutes: 30, buffer_minutes: 0, capacity: 3, week: weekFor(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], [{ start: "10:00", end: "12:00" }]),
    check_in: null, check_out: null, min_notice_minutes: 0, horizon_days: 60, closed_dates: [],
  };
  const day = "2099-11-05";
  const b = (id: string, name: string, over: object = {}) => ({ id, name, phone_masked: "••••3210", source: "phone" as const, ...over });
  const slot = (status: Slot["status"], bookings: Slot["bookings"], start = "10:00", end = "10:30"): Slot => ({ start: `${day}T${start}:00+05:30`, end: `${day}T${end}:00+05:30`, status, remaining: 3 - bookings.length, bookings, block_id: null });
  const data: SlotsResponse = {
    timezone: "Asia/Kolkata", kind: "slots",
    days: [{ date: day, open: true, slots: [
      slot("booked", [b("A1", "Sunita Kulkarni"), b("A2", "Ravi Menon")]),
      slot("full", [b("A3", "Sunita Kulkarni"), b("A4", "Ravi Menon"), b("A5", "Arjun Shah")], "10:30", "11:00"),
      slot("free", [], "11:00", "11:30"),
    ] }],
  };
  const calendar = (onOpen = jest.fn()) =>
    render(<WeekCalendar schedule={schedule} days={[day]} data={data} loading={false} nowMs={Date.now()} highlight={[]} compact onOpenSlot={onOpen} onBlockRange={jest.fn()} />);

  it("shows the people booked in a slot instead of only how many are left", () => {
    calendar();
    const grid = screen.getByRole("grid");
    expect(within(grid).getByText("Sunita K., Ravi M.")).toBeInTheDocument();
    expect(within(grid).getByText("Sunita K., Ravi M. +1")).toBeInTheDocument();
    expect(within(grid).queryByText(/of 3 left/)).toBeNull();
    expect(within(grid).getByRole("button", { name: /partly booked by Sunita Kulkarni, Ravi Menon, 1 of 3 left/ })).toBeInTheDocument();
  });

  it("shows a card with each person, their masked number and where they came from on hover and focus", () => {
    calendar();
    const cell = screen.getByRole("button", { name: /full, booked by Sunita Kulkarni, Ravi Menon, Arjun Shah/ });
    fireEvent.pointerEnter(cell);
    const card = screen.getByRole("tooltip");
    expect(within(card).getAllByText(/Sunita Kulkarni|Ravi Menon|Arjun Shah/)).toHaveLength(3);
    expect(within(card).getAllByText(/••••3210 · Phone call/)).toHaveLength(3);
    fireEvent.pointerLeave(cell);
    fireEvent.focus(screen.getByRole("button", { name: /partly booked/ }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Ravi Menon");
  });

  it("lets the slot menu open one person's booking", () => {
    const open = jest.fn();
    const target = { slot: data.days[0].slots[0], el: document.body, following: [] };
    render(<SlotPopover schedule={schedule} target={target} onClose={jest.fn()} onBlock={jest.fn()} onFree={jest.fn()} onBook={jest.fn()} onOpenBooking={open} />);
    fireEvent.click(screen.getByRole("button", { name: "Open booking A2 for Ravi Menon" }));
    expect(open).toHaveBeenCalledWith("A2");
  });

  it("names the guests of each night on the month grid", () => {
    const nightsSchedule: Schedule = { ...schedule, id: "S2", kind: "nights", capacity: 4, check_in: "14:00", check_out: "11:00", week: weekFor(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], [{ start: "00:00", end: "24:00" }]) };
    const d = nowIn("Asia/Kolkata", Date.now()).date;
    const stays: SlotsResponse = {
      timezone: "Asia/Kolkata", kind: "nights",
      days: [{ date: d, open: true, slots: [{ start: `${d}T14:00:00+05:30`, end: `${d}T11:00:00+05:30`, status: "booked", remaining: 1,
        bookings: [b("N1", "Arjun Shah", { nights: 2 }), b("N2", "Meera Iyer", { nights: 1 }), b("N3", "Kabir Sethi", { nights: 3 })], block_id: null }] }],
    };
    render(<NightsCalendar schedule={nightsSchedule} month={d} data={stays} loading={false} nowMs={Date.now()} highlight={[]} onOpenSlot={jest.fn()} />);
    expect(screen.getByText("Arjun S. · 2 nights")).toBeInTheDocument();
    expect(screen.getByText("+2 more")).toBeInTheDocument();
    expect(screen.getByText("1 left")).toBeInTheDocument();
  });
});
