import { render, screen, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import SettingsPage from "@/app/(app)/desk/settings/page";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() }, ApiError: class ApiError extends Error {}, API_URL: "http://api.test" }));
jest.mock("@/hooks/useApi", () => ({
  useHealth: () => ({ data: { llm: { active: "remote", model: "m1" }, supabase: { configured: false } }, isLoading: false }),
  useEngines: () => ({ data: [{ id: "timbre", name: "Timbre (Gnani)", available: true }], isLoading: false }),
  useAgents: () => ({ data: [], isLoading: false }),
  useConsentMode: () => ({ data: { consent_mode: "tick", statement: "I agree.", retention_days: 180, contact: null }, isLoading: false }),
}));
const get = api.get as jest.Mock;

const phone = (providers: unknown[]) => ({
  configured: true, missing: [], allowed_numbers: [], allowed_numbers_set: false, caller_id_masked: null, inbound_ready: true, max_call_seconds: 600, providers,
});
const usage = {
  window_days: 7, estimate_note: "", rates_verified: false, rates: {}, totals: { requests: 0, credits: 0, rupees: null, cache_hits: 0, cache_hit_rate: null, degraded: 0 },
  by_kind: {}, queue_wait: { p50_ms: null, p95_ms: null }, by_lane: {}, degraded: { count: 0, by_reason: {}, by_action: {}, hold_lines_played: 0, recent: [] }, calls: 0, cost_per_call: null,
  cost_per_call_rupees: null, bookings: 0, cost_per_booking: null, cost_per_booking_rupees: null, daily: [],
  budget: { budget: 5000, spent: 0, left: 5000, reserve_pct: 20, reserve: 1000, usable_for_desk: 4000, left_for_desk: 4000, reserve_reached: false, exhausted: false, rupees_per_credit: null, spent_rupees: null, left_rupees: null },
  providers: {
    live: { stt: { provider: "gnani", name: "Gnani Prisma", state: "needs_key", why: "No GNANI_API_KEY is set, so this cannot work yet." }, tts: { provider: "gnani", name: "Gnani Timbre", state: "needs_key", why: "No GNANI_API_KEY is set, so this cannot work yet." } },
    desk: { stt: { provider: "gnani", name: "Gnani Prisma", state: "needs_key", why: "No GNANI_API_KEY is set, so this cannot work yet." }, tts: { provider: "gnani", name: "Gnani Timbre", state: "needs_key", why: "No GNANI_API_KEY is set, so this cannot work yet." } },
  },
};

function serve(providers: unknown[]) {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/phone/status")) return phone(providers);
    if (path.startsWith("/api/usage/summary")) return usage;
    if (path.startsWith("/api/privacy/attestation")) return {};
    if (path.startsWith("/api/whatsapp/")) return {};
    throw new Error(`unexpected GET ${path}`);
  });
}
const view = () => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}><SettingsPage /></SWRConfig>);

describe("Settings: speech models and the telephony provider", () => {
  it("has a Speech models card between the providers and consent", async () => {
    serve([]);
    view();
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings.slice(0, 3)).toEqual(["Speech providers", "Speech models", "Consent"]);
    expect(await screen.findAllByText("No GNANI_API_KEY is set, so this cannot work yet.")).toHaveLength(4);
    for (const name of ["Speech providers", "Speech models"]) {
      const section = screen.getByRole("heading", { name }).closest("[aria-labelledby]") as HTMLElement;
      const text = (section.textContent ?? "").replace(/Language model.*$/, "");   // the language model row shows whatever the server reports
      expect(text).not.toMatch(/backup|local voice|second provider/i);
    }
  });

  it("shows the phone line as Vobiz, configured or not, with the settings that are missing", async () => {
    serve([]);
    get.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/phone/status")) return { ...phone([]), configured: false, missing: ["VOBIZ_AUTH_ID", "VOBIZ_CALLER_ID"] };
      if (path.startsWith("/api/usage/summary")) return usage;
      return {};
    });
    view();
    const section = screen.getByRole("heading", { name: "Phone provider" }).closest("[aria-labelledby]") as HTMLElement;
    const row = (await within(section).findByText("Vobiz")).closest("div") as HTMLElement;
    expect(within(row).getByText("Not configured")).toBeInTheDocument();
    expect(within(section).getByText("VOBIZ_AUTH_ID")).toBeInTheDocument();
    expect(within(section).getByText("VOBIZ_CALLER_ID")).toBeInTheDocument();
  });

  it("shows Vobiz as configured, and says when it has not been tried on a real call", async () => {
    serve([]);
    get.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/phone/status")) return { ...phone([]), provider_verified: false };
      if (path.startsWith("/api/usage/summary")) return usage;
      return {};
    });
    view();
    const section = screen.getByRole("heading", { name: "Phone provider" }).closest("[aria-labelledby]") as HTMLElement;
    const row = (await within(section).findByText("Vobiz")).closest("div") as HTMLElement;
    expect(within(row).getByText("Configured")).toBeInTheDocument();
    expect(within(row).getByText("Not yet tried on a real call")).toBeInTheDocument();
    expect(within(section).queryByText(/account/i)).not.toBeInTheDocument();
  });
});
