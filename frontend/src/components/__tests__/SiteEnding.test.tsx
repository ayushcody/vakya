import { render, screen, within } from "@testing-library/react";
import { Faq, FinalCta, Footer } from "@/components/site/Ending";

jest.mock("@/components/Logo", () => ({ Logo: () => <span>Vakya</span> }));

describe("landing ending", () => {
  it("renders the FAQ as native details with FAQPage JSON-LD that matches", () => {
    const { container } = render(<Faq />);
    const items = container.querySelectorAll("details");
    expect(items.length).toBeGreaterThanOrEqual(8);
    expect(screen.getByRole("heading", { name: /straight answers/i })).toBeInTheDocument();
    const ld = container.querySelector('script[type="application/ld+json"]');
    const data = JSON.parse(ld!.textContent!);
    expect(data["@type"]).toBe("FAQPage");
    expect(data.mainEntity).toHaveLength(items.length);
    expect(screen.getByRole("link", { name: "report card" })).toHaveAttribute("href", "/report-card");
  });

  it("offers the three closing actions", () => {
    render(<FinalCta />);
    expect(screen.getByRole("link", { name: /try a call/i })).toHaveAttribute("href", "/desk/try");
    expect(screen.getByRole("link", { name: /verify a clip/i })).toHaveAttribute("href", "/verify");
    expect(screen.getByRole("link", { name: /run it yourself/i })).toHaveAttribute("href", "/run-it");
  });

  it("answers the honest questions: nobody has used it, and what is not ready", () => {
    const { container } = render(<Faq />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/Has anyone used it\?/);
    expect(text).toMatch(/no customers, no clinics and no real callers/);
    expect(text).toMatch(/missed-call callback/);
    expect(text).not.toMatch(/Studio|WhatsApp|widget|RTX|Windows is untested/);
  });

  it("links every page, the desk and the receipt check from the footer, and nothing to the private repository", () => {
    render(<Footer />);
    const nav = screen.getByRole("navigation", { name: "Footer" });
    const hrefs = within(nav).getAllByRole("link").map((a) => a.getAttribute("href"));
    for (const h of ["/", "/how-it-works", "/report-card", "/run-it", "/faq", "/desk", "/desk/try", "/desk/calls", "/desk/report-card", "/verify"]) expect(hrefs).toContain(h);
    expect(hrefs.filter((h) => h?.includes("github.com"))).toEqual([]);
    expect(within(nav).getByRole("link", { name: "Run it" })).toHaveAttribute("href", "/run-it");
  });

  it("names the services it calls and keeps the receipt key link in the footer", () => {
    render(<Footer />);
    expect(screen.getByText(/Hosted language model and embeddings/)).toBeInTheDocument();
    expect(screen.getByText(/Vobiz \(telephony\)/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /receipt public key/i }).getAttribute("href")).toMatch(/\/\.well-known\/vakya-receipt-key$/);
  });

  it("has no photo credits (the language photos are gone) and states the Gnani speech stack without claiming a partnership", () => {
    const { container } = render(<Footer />);
    expect(Array.from(container.querySelectorAll("summary")).some((x) => /photo credits/i.test(x.textContent ?? ""))).toBe(false);
    expect(screen.getByText(/Speech stack: Gnani AI \(Prisma and Timbre\)\. Vakya is not affiliated with Gnani AI\./)).toBeInTheDocument();
  });

  it("lists the two-person team with a placeholder", () => {
    render(<Footer />);
    expect(screen.getByText("Ayush Chougula")).toBeInTheDocument();
    expect(screen.getByText("Teammate (TBC)")).toBeInTheDocument();
    expect(screen.queryByText(/Atharva|Arnav|Harpal|Neha/)).toBeNull();
  });

  it("sets the footer column headings as 12 px uppercase labels", () => {
    const { container } = render(<Footer />);
    const heads = Array.from(container.querySelectorAll(".vk-foot-h")).map((e) => e.textContent);
    expect(heads).toEqual(expect.arrayContaining(["Explore", "Product", "Built by", "Services", "Notices"]));
  });
});
