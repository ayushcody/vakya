import { render, screen } from "@testing-library/react";
import { Logo, LogoMark, Wordmark } from "../Logo";
import fs from "fs";
import path from "path";

describe("Logo", () => {
  it("defaults to the lockup, named Vakya, with five ink bars and a danda", () => {
    const { container } = render(<Logo />);
    const svg = screen.getByRole("img", { name: "Vakya" });
    expect(svg).toHaveAttribute("data-variant", "lockup");
    expect(container.querySelectorAll("rect")).toHaveLength(6);
    expect(container.querySelectorAll("path").length).toBeGreaterThan(0);
  });

  it("sizes by height and keeps the drawing's proportions", () => {
    const { rerender } = render(<Logo size={44} />);
    const lockup = screen.getByRole("img", { name: "Vakya" });
    expect(lockup).toHaveAttribute("height", "44");
    expect(Number(lockup.getAttribute("width"))).toBeCloseTo((44 * 314) / 88, 1);
    rerender(<Logo variant="mark" size={64} />);
    expect(screen.getByRole("img", { name: "Vakya" })).toHaveAttribute("width", "79");
  });

  it("draws mark, lockup and wordmark differently", () => {
    const { container, rerender } = render(<Logo variant="mark" />);
    expect(container.querySelectorAll("path")).toHaveLength(0);
    rerender(<Logo variant="wordmark" />);
    expect(container.querySelectorAll("rect")).toHaveLength(0);
    expect(container.querySelectorAll("circle")).toHaveLength(2);
  });

  it("applies the tone class and maps the legacy dark tone to light ink", () => {
    const { rerender } = render(<Logo tone="ink" />);
    expect(screen.getByRole("img")).toHaveClass("ink");
    rerender(<Logo tone="light" />);
    expect(screen.getByRole("img")).toHaveClass("light");
    rerender(<Logo tone="dark" />);
    expect(screen.getByRole("img")).toHaveClass("light");
  });

  it("keeps the legacy showText and onlyText props working", () => {
    const { rerender } = render(<Logo showText={false} />);
    expect(screen.getByRole("img")).toHaveAttribute("data-variant", "mark");
    rerender(<Logo onlyText />);
    expect(screen.getByRole("img")).toHaveAttribute("data-variant", "wordmark");
  });

  it("colours the danda through --accent with the brand violet as fallback", () => {
    const css = fs.readFileSync(path.join(process.cwd(), "src/components/Logo.module.css"), "utf8");
    expect(css).toMatch(/var\(--accent, #6c62ed\)/);
  });
});

describe("LogoMark and Wordmark", () => {
  it("LogoMark is decorative by default and named when given a label", () => {
    const { container, rerender } = render(<LogoMark size={40} />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector("svg")).toHaveAttribute("height", "40");
    rerender(<LogoMark size={40} label="Vakya" />);
    expect(screen.getByRole("img", { name: "Vakya" })).toBeInTheDocument();
  });

  it("Wordmark is the named wordmark drawing", () => {
    render(<Wordmark size={30} />);
    expect(screen.getByRole("img", { name: "Vakya" })).toHaveAttribute("data-variant", "wordmark");
  });
});
