import { act, render, screen, within } from "@testing-library/react";
import { SCRIPT_FONT } from "@/components/site/fonts";
import { Consent } from "@/components/site/Sections";
import { Architecture, TurnRules } from "@/components/site/Architecture";
import { DemoVideo } from "@/components/site/DemoVideo";
import { BuiltOnGnani, CallFlow, Limitations, ReportTeaser, StackStrip, Team, TrustGrid } from "@/components/site/Landing";
import { EXAMPLE_CALL, GNANI_MODELS, LIMITATIONS } from "@/components/site/submissionData";

jest.mock("@/components/Logo", () => ({ Logo: () => <span>Vakya</span> }));
jest.mock("@/components/site/SmoothScroll", () => ({ useLenis: () => null }));
jest.mock("gsap", () => {
  const mm = { add: jest.fn(), revert: jest.fn() };
  const gsap = { registerPlugin: jest.fn(), matchMedia: () => mm, utils: { toArray: () => [] } };
  return { gsap, default: gsap };
});
jest.mock("gsap/ScrollTrigger", () => ({ ScrollTrigger: { refresh: jest.fn() } }));
// DemoVideo asks the file system whether public/landing/demo.mp4 exists; the tests choose the answer.
let mockVideo = false;
jest.mock("node:fs", () => ({ existsSync: (p: string) => mockVideo && /demo(-poster)?\.(mp4|jpg)$/.test(p) }));

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (q: string) => ({ matches: false, media: q, addEventListener: jest.fn(), removeEventListener: jest.fn(), addListener: jest.fn(), removeListener: jest.fn() }),
  });
});

describe("landing sections", () => {
  it("shows what it is built on and the languages as plain tags", () => {
    render(<StackStrip />);
    const langs = screen.getByRole("list", { name: "Languages" });
    expect(within(langs).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Hindi", "Marathi", "English", "Code-mixed"]);
    expect(screen.getByText("Gnani Prisma")).toBeInTheDocument();
    expect(screen.getByText("Gnani Timbre")).toBeInTheDocument();
  });

  it("shows a placeholder frame that says when the demo lands, and plays the file once it exists", () => {
    mockVideo = false;
    const { container, rerender } = render(<DemoVideo />);
    expect(screen.getByText("Demo video: 9 Nov")).toBeInTheDocument();
    expect(container.querySelector("video")).toBeNull();
    mockVideo = true;
    rerender(<DemoVideo />);
    const v = container.querySelector("video")!;
    expect(v).not.toBeNull();
    expect(v.querySelector("source")).toHaveAttribute("src", "/landing/demo.mp4");
    expect(v).toHaveAttribute("poster", "/landing/demo-poster.jpg");
    expect(screen.queryByText("Demo video: 9 Nov")).toBeNull();
    mockVideo = false;
  });

  it("walks the call in four moves beside a read-back transcript where the caller corrects a digit", () => {
    const { container } = render(<CallFlow />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(/One call,\s*four moves\./);
    expect(container.querySelectorAll("ol")[0].querySelectorAll(":scope > li")).toHaveLength(4);
    const figure = screen.getByRole("figure", { name: /Example call/ });
    expect(within(figure).getAllByRole("listitem")).toHaveLength(EXAMPLE_CALL.length);
    expect(container.querySelectorAll("[lang=mr]").length).toBe(EXAMPLE_CALL.length);
    expect(figure).toHaveTextContent("Caller corrects one digit");
    expect(figure).toHaveTextContent("Spoken yes");
    expect(figure).toHaveTextContent(/it is not a recording/);
  });

  it("names Prisma, Timbre and cloning with their exact API names, and says they are not yet live-tested", () => {
    const { container } = render(<BuiltOnGnani />);
    expect(container.textContent).toContain("Integrated. Live numbers coming.");
    for (const m of GNANI_MODELS) for (const a of m.api) expect(container.textContent).toContain(a);
    expect(container.textContent).toContain("POST /stt/v3");
    expect(container.textContent).toContain("wss://api.vachana.ai/stt/v3/stream");
    expect(container.textContent).toContain("POST /api/v1/tts/inference");
    expect(container.textContent).toContain("vachana-vc-v1");
    expect(container.textContent).toMatch(/not yet been run against the live API/);
    expect(container.textContent).toMatch(/Embeddings Flash/);
    expect(container.textContent).toMatch(/no second speech model/i);
  });

  it("teases the report card as pending, with the method, an empty table and the caveats", () => {
    const { container } = render(<ReportTeaser />);
    expect(container.textContent).toMatch(/Gnani Prisma run pending/);
    expect(container.textContent).toMatch(/We publish no number until Gnani Prisma has heard them/);
    const table = screen.getByRole("table", { name: /without and with keyterms/ });
    expect(within(table).getAllByRole("rowheader").map((e) => e.textContent)).toEqual([
      "Clean",
      "Phone line, 8 kHz",
      "Phone codec, G.711",
      "Street noise, 5 dB",
      "Street noise, 0 dB",
      "Phone line and noise",
    ]);
    expect(within(table).getAllByText("pending")).toHaveLength(12);
    expect(container.textContent).not.toMatch(/\d+%/);
    expect(container.textContent).toMatch(/synthetic/);
    expect(container.textContent).toMatch(/9 of 9 scenarios pass, on a scripted model/);
    expect(screen.getByRole("link", { name: /Read the method/ })).toHaveAttribute("href", "/report-card");
  });

  it("lists the trust parts without invented counts, and a verify link", () => {
    const { container } = render(<TrustGrid />);
    expect(container.textContent).not.toMatch(/\d+\/\d+/);
    expect(container.textContent).toMatch(/no longer matches/i);
    expect(container.textContent).toMatch(/deleted by number/);
    expect(screen.getByRole("link", { name: /Verify a clip/ })).toHaveAttribute("href", "/verify");
  });

  it("states every limitation up front", () => {
    const { container } = render(<Limitations />);
    expect(container.querySelectorAll("li")).toHaveLength(LIMITATIONS.length);
    expect(container.textContent).toMatch(/Gnani models not yet run live/);
    expect(container.textContent).toMatch(/Missed-call callback not tried on a live number/);
    expect(container.textContent).toMatch(/Synthetic audio, simulated noise/);
  });

  it("shows the team with a placeholder teammate, and claims no users or partnership", () => {
    const { container } = render(<Team />);
    expect(screen.getByText("Ayush Chougula")).toBeInTheDocument();
    expect(screen.getByText("Teammate (TBC)")).toBeInTheDocument();
    expect(container.textContent).toContain("not affiliated with Gnani AI");
    expect(container.textContent).toContain("invented");
  });
});

describe("how it works", () => {
  it("draws the call path in five stages with honest status tags and the two side rails", () => {
    const { container } = render(<Architecture />);
    const path = screen.getByRole("list", { name: /Call path/ });
    const stages = Array.from(path.children).filter((li) => li.querySelector("h3"));
    expect(stages.map((li) => li.querySelector("h3")!.textContent)).toEqual([
      "Caller on a Vobiz line",
      "Gnani Prisma v3",
      "Turn manager",
      "Language model with tools",
      "Gnani Timbre",
    ]);
    expect(container.textContent).toContain("Consent, label, receipt");
    expect(container.textContent).toContain("Live monitor and takeover");
    expect(container.textContent).toContain("Being wired");
    // no timings are quoted until they are measured
    expect(container.textContent).not.toMatch(/\b\d+\.\d+ s\b/);
  });

  it("lists the turn rules", () => {
    const { container } = render(<TurnRules />);
    expect(container.querySelectorAll("dt")).toHaveLength(4);
    expect(container.textContent).toMatch(/reads back, then commits/i);
  });

  it("tells the consent story in four steps with the verify link and developer endpoints", () => {
    const { container } = render(<Consent />);
    expect(container.querySelector("section")).toHaveAttribute("id", "consent");
    expect(screen.getByRole("heading", { name: "Your voice. Your consent. Verifiable." })).toBeInTheDocument();
    expect(container.querySelectorAll("[data-cstep]")).toHaveLength(4);
    expect(screen.getByRole("link", { name: /open \/verify/i })).toHaveAttribute("href", "/verify");
    expect(screen.getByRole("link", { name: "/.well-known/vakya-receipt-key" }).getAttribute("href")).toMatch(/\/\.well-known\/vakya-receipt-key$/);
    expect(screen.getByRole("link", { name: "/api/withdrawals" }).getAttribute("href")).toMatch(/\/api\/withdrawals$/);
    // Without JS the final state is simply visible: nothing is hidden by the markup.
    expect(container.querySelector("[data-c-chip]")).not.toHaveAttribute("style");
  });
});

/** A stand-in IntersectionObserver that keeps its callbacks, so a test can say when a section comes near the screen. */
type FakeObserver = { cb: IntersectionObserverCallback; observed: Element[] };
const observers: FakeObserver[] = [];
class FakeIntersectionObserver {
  readonly root = null;
  readonly rootMargin = "";
  readonly thresholds = [];
  private record: FakeObserver;
  constructor(cb: IntersectionObserverCallback) {
    this.record = { cb, observed: [] };
    observers.push(this.record);
  }
  observe(el: Element) {
    this.record.observed.push(el);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
function nearScreen(el: Element) {
  const obs = observers.find((o) => o.observed.includes(el));
  if (!obs) throw new Error("no observer watches this element");
  act(() => obs.cb([{ isIntersecting: true, target: el } as IntersectionObserverEntry], {} as IntersectionObserver));
}

describe("the example call loads the Devanagari face near the screen", () => {
  const realIO = window.IntersectionObserver;
  beforeAll(() => {
    window.IntersectionObserver = FakeIntersectionObserver as unknown as typeof IntersectionObserver;
  });
  afterAll(() => {
    window.IntersectionObserver = realIO;
  });
  beforeEach(() => {
    observers.length = 0;
  });

  it("uses the page font until the frame nears the screen, then applies Devanagari", () => {
    const { container } = render(<CallFlow />);
    const mr = SCRIPT_FONT.mr;
    const line = container.querySelector("[lang=mr]")!;
    expect(line.classList.contains(mr)).toBe(false);
    nearScreen(container.querySelector("figure")!);
    expect(line.classList.contains(mr)).toBe(true);
  });
});
