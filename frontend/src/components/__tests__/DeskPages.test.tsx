import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import DeskToday from "@/app/(app)/desk/page";
import CallersPage from "@/app/(app)/desk/callers/page";
import TryPage from "@/app/(app)/desk/try/page";
import SettingsPage from "@/app/(app)/desk/settings/page";
import DeskReportCardPage from "@/app/(app)/desk/report-card/page";
import { api } from "@/lib/api";

let mockParams = new URLSearchParams();
jest.mock("next/navigation", () => ({ useSearchParams: () => mockParams, usePathname: () => "/desk", useRouter: () => ({ push: jest.fn(), replace: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), del: jest.fn() }, ApiError: class ApiError extends Error {}, API_URL: "http://api.test" }));
jest.mock("@/components/agents/WebCallPanel", () => ({ WebCallPanel: ({ agentId }: { agentId: string }) => <p>web call panel for {agentId}</p> }));
jest.mock("@/components/agents/CallDialog", () => ({
  CallDialog: ({ open, initialTab }: { open: boolean; initialTab?: string }) => (open ? <p>call dialog on {initialTab}</p> : null),
}));
jest.mock("@/hooks/useApi", () => ({
  useAgents: jest.fn(),
  useHealth: jest.fn(),
  useEngines: jest.fn(),
  useConsentMode: jest.fn(),
}));

import * as hooks from "@/hooks/useApi";

const get = api.get as jest.Mock;
// The clock is frozen (only Date; timers still run, so waitFor and SWR work): noon on Tuesday 3 November 2026 in Asia/Kolkata.
const FROZEN = new Date("2026-11-03T06:30:00.000Z");
const FAKE_DATE_ONLY = ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "setImmediate", "clearImmediate", "nextTick", "queueMicrotask", "requestAnimationFrame", "cancelAnimationFrame", "requestIdleCallback", "cancelIdleCallback", "performance", "hrtime"] as const;
const now = FROZEN.toISOString();
const yesterday = new Date(FROZEN.getTime() - 36 * 3600 * 1000).toISOString();
const agent = {
  id: "a1",
  name: "Sunrise Clinic",
  greeting: "Namaste",
  system_instructions: "You are the receptionist.",
  supported_languages: ["hi", "en"],
  voice_id: "v1",
  document_count: 2,
  created_at: "2026-10-01T00:00:00Z",
};
const call = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  agent_id: "a1",
  agent_name: "Sunrise Clinic",
  channel: "phone",
  practice: false,
  when: now,
  caller: "••••3210",
  duration_s: 95,
  replies: 3,
  preview: "I want to see the doctor",
  outcome: { key: "booked", label: "Booked", detail: "Booking A12", request_id: null },
  ...over,
});
/** Today in Asia/Kolkata (3 November) at h:59, as the server sends it. */
const todayAt = (h: number) => `2026-11-03T${String(h).padStart(2, "0")}:59:00+05:30`;
const nextWeek = new Date(FROZEN.getTime() + 7 * 86_400_000).toISOString();
const appt = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  name: "Priya Sharma",
  phone: "••••3210",
  service: "Dr. Meera Kulkarni",
  specialty: "General Physician",
  reason: "fever",
  start: nextWeek,
  timezone: "Asia/Kolkata",
  status: "booked",
  conversation_id: "c1",
  receipt_id: "r1",
  agent_id: "a1",
  created_at: now,
  calendar_sync: "not_configured",
  ...over,
});
// A10 starts today (booked on an earlier call), A11 next week and was made today, A12 is cancelled.
const bookings = [
  appt("A10", { start: todayAt(23), created_at: yesterday, conversation_id: "c2" }),
  appt("A11"),
  appt("A12", { status: "cancelled", conversation_id: null }),
];
const calls = [call("c1"), call("c2", { when: yesterday }), call("c3", { outcome: { key: "couldnt_answer", label: "Couldn't answer", detail: null, request_id: null } })];

function serve(extra: Record<string, unknown> = {}) {
  get.mockReset().mockImplementation(async (path: string) => {
    for (const [prefix, body] of Object.entries(extra)) if (path.startsWith(prefix)) return body;
    if (path.startsWith("/api/bookings")) return { bookings, timezone: "Asia/Kolkata" };
    if (path.startsWith("/api/calls/")) {
      return { ...calls[0], transcript: [{ role: "agent", text: "Booked.", at: now, actions: [{ name: "book_appointment", ok: true, summary: "booking A12 with Dr. Meera on Monday 2026-10-19 10:30" }] }] };
    }
    if (path.startsWith("/api/calls")) return { calls, days: 30 };
    if (path.startsWith("/api/phone/status")) return { configured: true, missing: [], allowed_numbers: [], allowed_numbers_set: false, caller_id_masked: null, inbound_ready: true, max_call_seconds: 600, providers: [] };
    throw new Error(`unexpected GET ${path}`);
  });
}
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

beforeEach(() => {
  jest.useFakeTimers({ now: FROZEN, doNotFake: [...FAKE_DATE_ONLY] });
  mockParams = new URLSearchParams();
  (hooks.useAgents as jest.Mock).mockReturnValue({ data: [agent], isLoading: false, mutate: jest.fn() });
  (hooks.useHealth as jest.Mock).mockReturnValue({ data: { llm: { active: "embeddings", model: "embeddings-flash" }, supabase: { configured: false } }, isLoading: false });
  (hooks.useEngines as jest.Mock).mockReturnValue({ data: [{ id: "timbre", name: "Timbre (Gnani)", available: true }], isLoading: false });
  (hooks.useConsentMode as jest.Mock).mockReturnValue({ data: { consent_mode: "tick", statement: "I agree.", retention_days: 180, contact: "owner@example.com" }, isLoading: false });
  serve();
});

afterEach(() => {
  jest.useRealTimers();
});

describe("Today", () => {
  it("leads with a Try a call button, counts appointments today and bookings made today from the bookings store", async () => {
    view(<DeskToday />);
    expect(screen.getByRole("heading", { level: 1, name: "Today" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Try a call" })).toHaveAttribute("href", "/desk/try");
    // A10 starts today; A11 was made today and is upcoming; A12 is cancelled and counts nowhere
    const section = await screen.findByRole("heading", { name: "Appointments today" });
    await waitFor(() => expect(screen.getByRole("button", { name: /Priya Sharma/ })).toBeInTheDocument());
    expect(screen.getByText("with Dr. Meera Kulkarni")).toBeInTheDocument();
    const tiles = screen.getAllByText(/^\d+$/).map((e) => e.textContent);
    expect(new Date().toISOString()).toBe(now); // the clock is frozen, so "23:59 today" is always still to come
    expect(tiles.slice(0, 4)).toEqual(["1", "1", "2", "2"]); // booked today, made today, calls today (c1 and c3), upcoming (A10 tonight and A11)
    expect(section).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All bookings/ })).toHaveAttribute("href", "/desk/bookings");
  });

  it("falls back to the booked calls when the bookings endpoint fails", async () => {
    const base = get.getMockImplementation()!;
    get.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/bookings")) throw new Error("no store");
      return base(path);
    });
    view(<DeskToday />);
    await waitFor(() => expect(screen.getByText("booking A12 with Dr. Meera on Monday 2026-10-19 10:30")).toBeInTheDocument());
  });

  it("lists recent calls, links each to its transcript, and says what needs attention from real data", async () => {
    (hooks.useAgents as jest.Mock).mockReturnValue({ data: [{ ...agent, document_count: 0 }], isLoading: false, mutate: jest.fn() });
    serve({ "/api/phone/status": { configured: false, missing: ["VOBIZ_AUTH_ID"] } });
    view(<DeskToday />);
    expect(await screen.findByText(/The front desk has no documents/)).toBeInTheDocument();
    expect(await screen.findByText(/Your phone line is not connected/)).toBeInTheDocument();
    expect(await screen.findByText(/1 call needs a person or could not be answered/)).toBeInTheDocument();
    const recent = screen.getByRole("heading", { name: "Recent calls" }).closest("section")!;
    await waitFor(() => expect(within(recent).getAllByRole("link", { name: /Booked|Couldn't answer/ }).length).toBeGreaterThan(0));
    expect(within(recent).getAllByRole("link").some((a) => a.getAttribute("href") === "/desk/calls?call=c1")).toBe(true);
  });

  it("says nothing needs attention when everything is set up", async () => {
    serve({ "/api/calls?": { calls: [call("c1")], days: 30 } });
    view(<DeskToday />);
    expect(await screen.findByText("Nothing needs your attention right now.")).toBeInTheDocument();
  });

  it("offers the setup checklist while the receptionist is not finished", async () => {
    (hooks.useAgents as jest.Mock).mockReturnValue({ data: [{ ...agent, voice_id: null }], isLoading: false, mutate: jest.fn() });
    view(<DeskToday />);
    expect(screen.getByRole("heading", { name: "Finish setting up" })).toBeInTheDocument();
  });
});

describe("Callers", () => {
  const profile = {
    id: "p1", number_masked: "••••3210", display_name: "Priya Sharma", preferred_language: "hi", last_call_at: now, last_outcome: "booked",
    last_booking: { date: "2026-11-10", time: "16:00", purpose: "dental check-up" }, follow_ups: [], summary: null, call_count: 2, calls: [],
    consent_note: "caller details kept to serve them on their next call", retention_days: 180, expires_at: now,
  };

  it("lists the remembered callers with a way to forget each", async () => {
    serve({ "/api/callers": { enabled: true, callers: [profile], total: 1, limit: 200, offset: 0 } });
    view(<CallersPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Callers" })).toBeInTheDocument();
    expect(await screen.findByText("Priya Sharma")).toBeInTheDocument();
    expect(screen.getByText("••••3210")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("row", { name: /Priya Sharma/ }));
    expect(await screen.findByRole("button", { name: "Forget this caller" })).toBeInTheDocument();
  });

  it("says when nobody is remembered yet, and when memory is switched off", async () => {
    serve({ "/api/callers": { enabled: true, callers: [], total: 0, limit: 200, offset: 0 } });
    const { unmount } = view(<CallersPage />);
    expect(await screen.findByText("No callers remembered yet")).toBeInTheDocument();
    unmount();
    serve({ "/api/callers": { enabled: false, callers: [], total: 0, limit: 200, offset: 0 } });
    view(<CallersPage />);
    expect(await screen.findByText("Caller memory is switched off")).toBeInTheDocument();
  });
});

describe("Try a call", () => {
  it("shows the browser web call for the receptionist and opens the phone dialog on its call-me tab", async () => {
    view(<TryPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Try a call" })).toBeInTheDocument();
    expect(screen.getByText("web call panel for a1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Call me" }));
    expect(screen.getByText("call dialog on phone")).toBeInTheDocument();
  });

  it("takes the receptionist from ?agent= when there are several", () => {
    (hooks.useAgents as jest.Mock).mockReturnValue({ data: [agent, { ...agent, id: "a2", name: "Dental desk" }], isLoading: false, mutate: jest.fn() });
    mockParams = new URLSearchParams("agent=a2");
    view(<TryPage />);
    expect(screen.getByText("web call panel for a2")).toBeInTheDocument();
    expect(screen.getByLabelText("Front desk")).toHaveValue("a2");
  });

  it("sends someone with no receptionist to set one up", () => {
    (hooks.useAgents as jest.Mock).mockReturnValue({ data: [], isLoading: false, mutate: jest.fn() });
    view(<TryPage />);
    expect(screen.getByRole("link", { name: "Set up a front desk" })).toHaveAttribute("href", "/desk/receptionist");
  });
});

describe("Report card", () => {
  it("renders the stress-test report with its caveat inside the desk", () => {
    view(<DeskReportCardPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Report card" })).toBeInTheDocument();
    expect(screen.queryByText("Gnani Prisma run pending")).toBeNull();
    expect(screen.getByRole("table", { name: /Task completion by condition/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Public page/ })).toHaveAttribute("href", "/report-card");
  });
});

describe("Settings", () => {
  const phone = {
    configured: true, missing: [], allowed_numbers: [{ number: "+919800000000", masked: "+91 ••••••0000", label: "Owner", source: "env", id: null }], allowed_numbers_set: true,
    caller_id_masked: "+91 ••••••1111", inbound_ready: false, public_base_url: null, max_call_seconds: 600,
    providers: [{ name: "vobiz", display: "Vobiz", configured: true, enabled: true, verified: false }], provider_verified: false,
  };

  it("shows the Gnani key, the provider in use, consent, the phone provider and privacy, without any secret", async () => {
    serve({ "/api/phone/status": phone, "/api/privacy/attestation": { statement: { claim: "configured_to_keep_data_on_this_machine" }, key_id: "k1" } });
    view(<SettingsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    const providers = screen.getByRole("heading", { name: "Speech providers" }).closest("[aria-labelledby]") as HTMLElement;
    expect(within(providers).getByText("Set")).toBeInTheDocument();
    expect(within(providers).getByText("Gnani Prisma")).toBeInTheDocument();
    expect(within(providers).getByText("Gnani Timbre")).toBeInTheDocument();
    const consent = screen.getByRole("heading", { name: "Consent" }).closest("[aria-labelledby]") as HTMLElement;
    expect(within(consent).getByText(/One tick/)).toBeInTheDocument();
    const phoneSection = screen.getByRole("heading", { name: "Phone provider" }).closest("[aria-labelledby]") as HTMLElement;
    expect(await within(phoneSection).findByText("Vobiz")).toBeInTheDocument();
    expect(within(phoneSection).getByText("Configured")).toBeInTheDocument();
    expect(within(phoneSection).getByText("Not yet tried on a real call")).toBeInTheDocument();
    expect(within(phoneSection).getByText("+91 ••••••0000")).toBeInTheDocument();
    expect(await screen.findByText("Configured to keep data on this computer")).toBeInTheDocument();
  });

  it("says the Gnani key is not set, and why, when the engine list says so", () => {
    (hooks.useEngines as jest.Mock).mockReturnValue({ data: [{ id: "timbre", name: "Timbre (Gnani)", available: false, unavailable_reason: "GNANI_API_KEY is not set" }], isLoading: false });
    serve({ "/api/phone/status": phone, "/api/privacy/attestation": {} });
    view(<SettingsPage />);
    expect(screen.getByText("Not set")).toBeInTheDocument();
    expect(screen.getByText("GNANI_API_KEY is not set")).toBeInTheDocument();
    expect(screen.getAllByText("Needs a Gnani key")).toHaveLength(3);
  });
});
