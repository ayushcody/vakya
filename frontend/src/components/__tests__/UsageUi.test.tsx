import { render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import type { ReactElement } from "react";
import { CallDetails } from "@/app/(app)/desk/calls/CallRow";
import { BudgetBar, SpeechModelsCard } from "@/components/usage/SpeechModelsCard";
import { Sparkline, UsageTiles } from "@/components/usage/UsageTiles";
import { CallUsageLine } from "@/components/usage/CallUsageLine";
import { degradedLabel, formatCredits, formatMs, formatPercent, formatRupees, latencyLine } from "@/components/usage/format";
import type { BudgetStatus, UsageSummary } from "@/components/usage/types";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), del: jest.fn() }, ApiError: class ApiError extends Error {} }));
const get = api.get as jest.Mock;

const budget = (over: Partial<BudgetStatus> = {}): BudgetStatus => ({
  budget: 5000, spent: 1250, left: 3750, reserve_pct: 20, reserve: 1000, usable_for_desk: 4000, left_for_desk: 2750,
  reserve_reached: false, exhausted: false, rupees_per_credit: null, spent_rupees: null, left_rupees: null, ...over,
});
const kind = (over = {}) => ({
  requests: 10, ok: 10, credits: 20, seconds: 30, chars: 800, cache_hits: 2, cache_hit_rate: 0.2, latency_p50_ms: 610, latency_p95_ms: 1240,
  queue_p50_ms: 5, queue_p95_ms: 400, degraded: 0, ...over,
});
const summary = (over: Partial<UsageSummary> = {}): UsageSummary => ({
  window_days: 7, estimate_note: "x", rates_verified: false, rates: {},
  totals: { requests: 24, credits: 38.45, rupees: null, cache_hits: 4, cache_hit_rate: 0.17, degraded: 2 },
  by_kind: { stt: kind(), tts: kind({ latency_p50_ms: 420, latency_p95_ms: 880 }) },
  queue_wait: { p50_ms: 8, p95_ms: 1500 }, by_lane: {},
  degraded: { count: 2, by_reason: { busy: 1, breaker_open: 1 }, by_action: { hold_line: 1, callback: 1 }, hold_lines_played: 2, recent: [
    { ts: 1_800_000_000, kind: "tts", lane: "live", reason: "busy", action: "hold_line", message: null, conversation_id: "c1" },
    { ts: 1_799_990_000, kind: "stt", lane: "live", reason: "breaker_open", action: "callback", message: null, conversation_id: "c2" }] },
  calls: 6, cost_per_call: 6.4, cost_per_call_rupees: null, bookings: 4, cost_per_booking: 9.6, cost_per_booking_rupees: null,
  daily: [0, 2, 5, 3, 8, 10, 10.45].map((credits, i) => ({ date: `2026-11-0${i + 1}`, credits, requests: 3 })),
  budget: budget(),
  providers: {
    live: { stt: { provider: "gnani", name: "Gnani Prisma", state: "ready", why: "GNANI_API_KEY is set." }, tts: { provider: "gnani", name: "Gnani Timbre", state: "unavailable", why: "Gnani paused after 5 failures (server_error); callers are offered a callback. Retrying in 12 s." } },
    desk: { stt: { provider: "gnani", name: "Gnani Prisma", state: "ready", why: "GNANI_API_KEY is set." }, tts: { provider: "gnani", name: "Gnani Timbre", state: "ready", why: "GNANI_API_KEY is set." } },
  },
  ...over,
});

function serve(body: unknown) {
  get.mockReset().mockImplementation(async (path: string) => {
    if (path.startsWith("/api/usage/summary")) {
      if (body instanceof Error) throw body;
      return body;
    }
    throw new Error(`unexpected GET ${path}`);
  });
}
const view = (ui: ReactElement) => render(<SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>);

describe("format helpers", () => {
  it("shows credits with fewer digits as they grow, and says nothing it does not know", () => {
    expect(formatCredits(0.0388)).toBe("0.04");
    expect(formatCredits(12.34)).toBe("12.3");
    expect(formatCredits(1234.5)).toBe("1,235");
    expect(formatCredits(null)).toBe("—");
    expect(formatRupees(12.5)).toBe("₹12.50");
    expect(formatRupees(undefined)).toBe("—");
    expect(formatMs(610)).toBe("610 ms");
    expect(formatMs(1240)).toBe("1.2 s");
    expect(formatPercent(0.171)).toBe("17%");
    expect(formatPercent(null)).toBe("—");
  });

  it("describes what happened when Gnani could not answer, never naming another model", () => {
    expect(degradedLabel("busy", "hold_line")).toBe("Gnani busy, retrying");
    expect(degradedLabel("rate_limited", "queued")).toBe("Gnani busy, retrying");
    expect(degradedLabel("breaker_open", "callback")).toBe("Gnani unavailable, offered a callback");
    expect(degradedLabel("breaker_open", "callback_offered")).toBe("Gnani unavailable, offered a callback");
    expect(degradedLabel("server_error", "queued")).toBe("Gnani unavailable, queued to retry");
    expect(degradedLabel("budget_reserve", "held_back")).toMatch(/reserve/);
    expect(degradedLabel(null)).toBe("Gnani busy, retrying");
    expect(latencyLine(kind())).toBe("p50 610 ms · p95 1.2 s");
    expect(latencyLine(kind({ latency_p50_ms: null }))).toBeNull();
    expect(latencyLine(undefined)).toBeNull();
  });
});

describe("BudgetBar", () => {
  it("fills to the amount used and marks the reserve where it starts", () => {
    render(<BudgetBar budget={budget()} />);
    const meter = screen.getByRole("meter", { name: "Credits used" });
    expect(meter).toHaveAttribute("aria-valuenow", "1250");
    expect(meter).toHaveAttribute("aria-valuemax", "5000");
    expect(meter.getAttribute("aria-valuetext")).toBe("1,250 of 5,000 credits used. The last 1,000 are kept for live calls.");
    expect(screen.getByTestId("reserve-mark")).toHaveStyle({ left: "80%" });
    expect(meter.firstElementChild).toHaveStyle({ width: "25%" });
    expect(screen.getByText("reserve 1,000 for live calls")).toBeInTheDocument();
  });

  it("turns warning inside the reserve and error when everything is gone, and survives a zero budget", () => {
    const { rerender, container } = render(<BudgetBar budget={budget({ spent: 4500, reserve_reached: true })} />);
    expect(container.querySelector(".bg-accent-warning")).toBeInTheDocument();
    rerender(<BudgetBar budget={budget({ spent: 5000, exhausted: true, reserve_reached: true })} />);
    expect(container.querySelector(".bg-accent-error")).toBeInTheDocument();
    rerender(<BudgetBar budget={budget({ budget: 0, spent: 0, reserve: 0, reserve_pct: 0 })} />);
    expect(screen.queryByTestId("reserve-mark")).not.toBeInTheDocument();
  });
});

describe("SpeechModelsCard", () => {
  it("shows the provider per lane and why, the credits, the speed, the cache and the busy-or-down history, as estimates", async () => {
    serve(summary());
    view(<SpeechModelsCard />);
    const card = (await screen.findByRole("heading", { name: "Speech models" })).closest("[aria-labelledby]") as HTMLElement;
    await within(card).findByText(/Gnani paused after 5 failures/);
    const hears = within(card).getByText("Live calls hear with").closest("div") as HTMLElement;
    expect(within(hears).getByText("Gnani Prisma")).toBeInTheDocument();
    const speaks = within(card).getByText("Live calls speak with").closest("div") as HTMLElement;
    expect(within(speaks).getByText("Gnani Timbre")).toBeInTheDocument();
    expect(within(speaks).getByText("unavailable")).toBeInTheDocument();
    expect(within(speaks).getByText(/callers are offered a callback/)).toBeInTheDocument();
    expect(within(card).getByText("Estimated from default rates that have not been measured on this account yet.")).toBeInTheDocument();
    expect(within(card).getByText(/3,750/)).toBeInTheDocument();
    expect(within(card).getByRole("meter")).toBeInTheDocument();
    expect(within(card).getByText(/p50 610 ms · p95 1\.2 s/)).toBeInTheDocument();
    expect(within(card).getByText(/p50 8 ms · p95 1\.5 s/)).toBeInTheDocument();
    expect(within(card).getByText("17% of requests")).toBeInTheDocument();
    expect(within(card).getByText(/About/)).toBeInTheDocument();
    expect(within(card).getByText(/a held line was played 2 times/)).toBeInTheDocument();
    const history = within(card).getByRole("list", { name: "Recent times Gnani could not answer" });
    expect(within(history).getByText("Gnani busy, retrying")).toBeInTheDocument();
    expect(within(history).getByText("Gnani unavailable, offered a callback")).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/backup|local model|second provider/i);
  });

  it("says measured rates when they are, shows rupees when a rate is set, and says when only the reserve is left", async () => {
    serve(summary({ rates_verified: true, budget: budget({ spent: 4200, left: 800, left_for_desk: 0, reserve_reached: true, left_rupees: 320, rupees_per_credit: 0.4 }), cost_per_booking_rupees: 3.84,
      degraded: { count: 0, by_reason: {}, by_action: {}, hold_lines_played: 0, recent: [] } }));
    view(<SpeechModelsCard />);
    expect(await screen.findByText("Estimated from measured rates.")).toBeInTheDocument();
    expect(screen.getByText(/about ₹320\.00/)).toBeInTheDocument();
    expect(screen.getByText(/only the reserve is left/)).toBeInTheDocument();
    expect(screen.getByText(/₹3\.84/)).toBeInTheDocument();
    expect(screen.getByText("Never in the last 7 days")).toBeInTheDocument();
  });

  it("says plainly when there are no bookings or timings yet, or when the numbers cannot be loaded", async () => {
    serve(summary({ cost_per_booking: null, bookings: 0, by_kind: {}, queue_wait: { p50_ms: null, p95_ms: null } }));
    const { unmount } = view(<SpeechModelsCard />);
    expect(await screen.findByText("No bookings made on calls in this period yet")).toBeInTheDocument();
    expect(screen.getByText("Nothing measured yet")).toBeInTheDocument();
    expect(screen.getByText("No waiting measured yet")).toBeInTheDocument();
    unmount();
    serve(new Error("down"));
    view(<SpeechModelsCard />);
    expect(await screen.findByRole("status")).toHaveTextContent("not available yet");
  });
});

describe("Today tiles", () => {
  it("shows the cost per booking in credits with an honest hint, a sparkline of the week and the busy-or-down count", async () => {
    serve(summary());
    view(<UsageTiles />);
    const tile = screen.getByTestId("cost-per-booking");
    await waitFor(() => expect(within(tile).getByText("9.60")).toBeInTheDocument());
    expect(within(tile).getByText("credits")).toBeInTheDocument();
    expect(within(tile).getByText("over 4 bookings in 7 days, estimated from default rates, not yet measured")).toBeInTheDocument();
    const spark = screen.getByTestId("usage-sparkline");
    const img = within(spark).getByRole("img");
    expect(img.getAttribute("aria-label")).toMatch(/^Credits per day, last 7 days: 0\.00, 2\.00, 5\.00/);
    expect(within(spark).getByText("38.5")).toBeInTheDocument();
    expect(within(screen.getByTestId("degraded-count")).getByText("2")).toBeInTheDocument();
  });

  it("uses rupees when a rate is set and says 'measured' when the rates are", async () => {
    serve(summary({ rates_verified: true, cost_per_booking_rupees: 3.84 }));
    view(<UsageTiles />);
    const tile = screen.getByTestId("cost-per-booking");
    await waitFor(() => expect(within(tile).getByText("₹3.84")).toBeInTheDocument());
    expect(within(tile).queryByText("credits")).not.toBeInTheDocument();
    expect(within(tile).getByText(/estimated from measured rates/)).toBeInTheDocument();
  });

  it("shows a dash, not a made-up zero, when there is no booking or the numbers are missing", async () => {
    serve(summary({ cost_per_booking: null, bookings: 0 }));
    const { unmount } = view(<UsageTiles />);
    await waitFor(() => expect(within(screen.getByTestId("cost-per-booking")).getByText("no bookings on calls in 7 days yet")).toBeInTheDocument());
    expect(within(screen.getByTestId("cost-per-booking")).getByText("—")).toBeInTheDocument();
    unmount();
    serve(new Error("down"));
    view(<UsageTiles />);
    await waitFor(() => expect(within(screen.getByTestId("degraded-count")).getByText("—")).toBeInTheDocument());
    expect(within(screen.getByTestId("cost-per-booking")).getByText("not available yet")).toBeInTheDocument();
  });

  it("draws one point for a single day and nothing for none", () => {
    const { container, rerender } = render(<Sparkline values={[3]} label="one day" />);
    expect(container.querySelector("line")).toBeInTheDocument();
    expect(container.querySelector("polyline")).not.toBeInTheDocument();
    rerender(<Sparkline values={[0, 0, 0]} label="flat" />);
    expect(container.querySelector("polyline")).toBeInTheDocument();
  });
});

describe("calls list", () => {
  const ctx = { tz: "Asia/Kolkata", today: "2026-11-04", known: {}, usage: {}, live: new Set<string>() };
  const call = {
    id: "c1", agent_id: "a1", agent_name: "Clinic desk", channel: "phone" as const, practice: false, when: new Date().toISOString(),
    caller: null, duration_s: 90, replies: 2, preview: "Hello", outcome: { key: "answered" as const, label: "Answered", detail: null, request_id: null },
  };
  const CallRow = ({ call: c }: { call: typeof call }) => <CallDetails id={c.id} summary={c} ctx={ctx} onClose={() => {}} />;
  const serveCalls = (calls: Record<string, unknown>) =>
    get.mockReset().mockImplementation(async (path: string) => {
      if (path.startsWith("/api/usage/calls")) return { days: 30, estimated: true, calls };
      throw new Error(`unexpected GET ${path}`);
    });

  it("shows the credits a call used, marked as an estimate, and when Gnani was busy or down", async () => {
    serveCalls({ c1: { credits: 3.456, requests: 6, cache_hits: 1, degraded: 2, degraded_reasons: ["busy", "timeout"], actions: ["hold_line"] } });
    view(<CallRow call={call} />);
    expect(await screen.findByText("About 3.46 credits (estimated)")).toBeInTheDocument();
    const badge = screen.getByText("Gnani busy, retrying ×2");
    expect(badge).toHaveAttribute("title", "busy, timeout");
  });

  it("says the callback was offered when Gnani was unavailable during a call", async () => {
    serveCalls({ c1: { credits: 1, requests: 3, cache_hits: 0, degraded: 1, degraded_reasons: ["breaker_open"], actions: ["callback", "callback_offered"] } });
    view(<CallRow call={call} />);
    expect(await screen.findByText("Gnani unavailable, offered a callback")).toBeInTheDocument();
  });

  it("says no credits when a call was answered from memory, and shows nothing without usage", async () => {
    serveCalls({ c1: { credits: 0, requests: 2, cache_hits: 2, degraded: 0, degraded_reasons: [], actions: [] } });
    const { unmount } = view(<CallRow call={call} />);
    expect(await screen.findByText("No credits used (answered from memory)")).toBeInTheDocument();
    expect(screen.queryByText(/Gnani busy|Gnani unavailable/)).not.toBeInTheDocument();
    unmount();
    serveCalls({});
    view(<CallUsageLine callId="c1" />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByTestId("call-usage")).not.toBeInTheDocument();
  });
});
