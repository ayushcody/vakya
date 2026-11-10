import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import SiteLayout from "@/app/(site)/layout";
import HomePage, { metadata as homeMeta } from "@/app/(site)/home/page";
import HowItWorksPage, { metadata as howMeta } from "@/app/(site)/how-it-works/page";
import ReportCardPage, { metadata as reportMeta } from "@/app/(site)/report-card/page";
import RunItPage, { metadata as runMeta } from "@/app/(site)/run-it/page";
import FaqPage, { metadata as faqMeta } from "@/app/(site)/faq/page";
import sitemap from "@/app/sitemap";
import robots from "@/app/robots";
import { ALL_SITE_PAGES, isSitePath, nextPage } from "@/components/site/routes";

let mockPath = "/";
jest.mock("next/navigation", () => ({ usePathname: () => mockPath, useRouter: () => ({ replace: jest.fn(), push: jest.fn() }) }));
jest.mock("@/components/Logo", () => ({ Logo: () => <span>Vakya</span> }));
jest.mock("node:fs", () => ({ existsSync: () => false }));
jest.mock("@/components/site/fonts", () => ({ SCRIPT_FONT: new Proxy({}, { get: () => "" }), useScriptFontsNear: () => true }));
// The real SmoothScroll drives Lenis and ScrollTrigger; here only its context and children matter.
jest.mock("@/components/site/SmoothScroll", () => ({
  useLenis: () => null,
  SmoothScroll: ({ children }: { children: ReactNode }) => <>{children}</>,
}));
jest.mock("gsap", () => {
  const chain: Record<string, unknown> = {};
  const tl = new Proxy(chain, { get: (_t, k) => (k === "then" ? undefined : () => tl) });
  const mm = { add: jest.fn(), revert: jest.fn() };
  const gsap = {
    registerPlugin: jest.fn(),
    matchMedia: () => mm,
    context: jest.fn(),
    fromTo: jest.fn(),
    to: jest.fn(),
    set: jest.fn(),
    timeline: () => tl,
    utils: { toArray: () => [], selector: () => () => [] },
  };
  return { gsap, default: gsap };
});
jest.mock("gsap/ScrollTrigger", () => ({ ScrollTrigger: { refresh: jest.fn(), create: jest.fn(), config: jest.fn() } }));

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener: jest.fn(), removeEventListener: jest.fn(), addListener: jest.fn(), removeListener: jest.fn() }),
  });
});

type Case = { path: string; page: () => ReactNode; h1: RegExp | string; navLabel: string | null; meta: { title?: unknown; description?: unknown } };

const CASES: Case[] = [
  { path: "/home", page: () => <HomePage />, h1: /Call\s+once,\s*it's\s+booked\./, navLabel: null, meta: homeMeta },
  { path: "/how-it-works", page: () => <HowItWorksPage />, h1: "From a ringing phone to a signed receipt.", navLabel: "How it works", meta: howMeta },
  { path: "/report-card", page: () => <ReportCardPage />, h1: "How it will hear a booking on a bad line.", navLabel: "Report card", meta: reportMeta },
  { path: "/run-it", page: () => <RunItPage />, h1: "Run it yourself.", navLabel: "Run it", meta: runMeta },
  { path: "/faq", page: () => <FaqPage />, h1: "Questions people ask.", navLabel: "FAQ", meta: faqMeta },
];

function renderAt(path: string, page: () => ReactNode) {
  mockPath = path;
  return render(<SiteLayout>{page()}</SiteLayout>);
}

describe.each(CASES)("page $path", ({ path, page, h1, navLabel }) => {
  it("has exactly one h1, the content landmark and the shared footer", () => {
    const { container } = renderAt(path, page);
    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0].textContent).toMatch(h1);
    expect(container.querySelector("#content")).not.toBeNull();
    expect(screen.getByRole("contentinfo", { name: "Site footer" })).toBeInTheDocument();
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1);
  });

  it("marks the right nav item (and only it) as the current page", () => {
    renderAt(path, page);
    const bar = screen.getByRole("navigation", { name: "Main" });
    const current = Array.from(bar.querySelectorAll("[aria-current='page']"));
    if (navLabel === null) {
      expect(current.map((e) => e.getAttribute("aria-label"))).toEqual(["Vakya home"]);
    } else {
      expect(current).toHaveLength(1);
      expect(current[0].textContent).toBe(navLabel);
    }
  });
});

describe("page content", () => {
  it("Home reads as a submission page: hero, stack strip, demo slot, call flow, front desks, Gnani, report card, trust, limits, try-it, team", () => {
    const { container } = renderAt("/home", () => <HomePage />);
    const ids = ["top", "demo", "call", "desks", "gnani", "measured", "trust", "limits", "start", "team"];
    for (const id of ids) expect(container.querySelector(`#${id}`)).not.toBeNull();
    // the sections come in reading order
    ids.slice(1).forEach((id, i) => {
      const before = container.querySelector(`#${ids[i]}`)!;
      expect(before.compareDocumentPosition(container.querySelector(`#${id}`)!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });
    // the old voice strip, product cards and page cards are gone
    for (const id of ["hear", "products", "built-for", "go-deeper", "callbacks", "in-numbers", "languages", "why"]) expect(container.querySelector(`#${id}`)).toBeNull();
    // the strip of front-desk kinds shows all eight, with the one line of copy
    expect(container.querySelectorAll("#desks li")).toHaveLength(8);
    expect(container.querySelector("#desks")).toHaveTextContent("Pick yours, name it, and it answers.");
    // the demo slot is honest when there is no video
    expect(container.querySelector("#demo")).toHaveTextContent("Demo video: 9 Nov");
    // the main call to action goes to the web call, with no partnership claim
    for (const a of screen.getAllByRole("link", { name: "Try a call" })) expect(a).toHaveAttribute("href", "/desk/try");
    expect(container.querySelector("#gnani")).toHaveTextContent("Live on Gnani since 5 Nov.");
    expect(container.querySelector("#team")).toHaveTextContent("not affiliated with Gnani AI");
    // nothing from the removed platform
    expect(container.textContent).not.toMatch(/widget|Hear a voice/i);
    expect(container.textContent).not.toMatch(/\bStudio\b/); // the product page; the lower-case "Gym and studio" desk is fine
    // no calendar claim as fact
    expect(container.textContent).not.toMatch(/books appointments into Google Calendar/i);
  });

  it("/how-it-works holds the call path, the turn rules and consent, then a Next link to the report card", () => {
    const { container } = renderAt("/how-it-works", () => <HowItWorksPage />);
    for (const id of ["how", "turns", "consent"]) expect(container.querySelector(`section#${id}`)).not.toBeNull();
    expect(within(screen.getByRole("navigation", { name: "Next page" })).getByRole("link")).toHaveAttribute("href", "/report-card");
  });

  it("/report-card holds the method, the measured stress-test tables and no pending note, then a Next link to Caller Theatre", () => {
    renderAt("/report-card", () => <ReportCardPage />);
    expect(screen.queryByText("Gnani Prisma run pending")).toBeNull();
    expect(screen.getByText(/Measured on Gnani Prisma v3.0 on 6 Nov 2026/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Task completion by condition/ })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Share of each field found/ })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Next page" })).getByRole("link")).toHaveAttribute("href", "/theatre");
  });

  it("/run-it shows the five commands, the ports and the keys, and a Next link to the FAQ", () => {
    const { container } = renderAt("/run-it", () => <RunItPage />);
    expect(container.querySelector("section#run-it")).not.toBeNull();
    expect(screen.getByText("./vakya start")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Environment settings/ })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Next page" })).getByRole("link")).toHaveAttribute("href", "/faq");
  });

  it("/faq holds the questions and the closing call to action, then a way back to Home", () => {
    const { container } = renderAt("/faq", () => <FaqPage />);
    expect(container.querySelectorAll("details.faq-item").length).toBeGreaterThanOrEqual(8);
    expect(container.querySelector("section#start")).not.toBeNull();
    expect(within(screen.getByRole("navigation", { name: "Next page" })).getByRole("link")).toHaveAttribute("href", "/home");
  });
});

describe("metadata", () => {
  it("keeps the exact Home title and gives every other page its own title and description", () => {
    expect(homeMeta.title).toEqual({ absolute: "Vakya: call once, it's booked" });
    const titles = CASES.map((c) => JSON.stringify(c.meta.title));
    const descriptions = CASES.map((c) => String(c.meta.description));
    expect(new Set(titles).size).toBe(CASES.length);
    expect(new Set(descriptions).size).toBe(CASES.length);
    for (const c of CASES.slice(1)) {
      expect(JSON.stringify(c.meta.title)).toMatch(/ \| Vakya/);
      expect(String(c.meta.description).length).toBeGreaterThan(40);
    }
  });
});

describe("routes, sitemap and robots", () => {
  it("lists the six public pages in the sitemap and points robots at it", () => {
    const urls = sitemap().map((e) => e.url);
    expect(urls).toHaveLength(6);
    for (const p of ["/how-it-works", "/report-card", "/theatre", "/run-it", "/faq"]) expect(urls.some((u) => u.endsWith(p))).toBe(true);
    expect(urls.some((u) => /\/(desk|dashboard|studio|verify)/.test(u))).toBe(false);
    const r = robots();
    expect(String(r.sitemap)).toMatch(/\/sitemap\.xml$/);
    expect(JSON.stringify(r.rules)).toContain("/desk");
  });

  it("treats only the marketing pages as site paths and chains them in reading order", () => {
    for (const p of ALL_SITE_PAGES) expect(isSitePath(p.href)).toBe(true);
    expect(isSitePath("/faq/")).toBe(true);
    for (const p of ["/desk", "/desk/calls", "/verify", "/login", "/register", "/proof", "/channels", "/nope"]) expect(isSitePath(p)).toBe(false);
    expect(nextPage("/home")?.href).toBe("/how-it-works");
    expect(nextPage("/run-it")?.href).toBe("/faq");
    expect(nextPage("/faq")).toBeUndefined();
  });
});
