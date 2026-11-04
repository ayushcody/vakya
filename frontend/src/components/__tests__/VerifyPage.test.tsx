import { readFileSync } from "fs";
import { join } from "path";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import VerifyPage from "@/app/verify/page";
import type { VerifyResponse } from "@/components/verify/types";

jest.mock("next/link", () => ({ __esModule: true, default: ({ children, href }: { children: ReactNode; href: string }) => <a href={href}>{children}</a> }));
jest.mock("@/components/Logo", () => ({ Logo: () => <span>Vakya</span> }));

const SRC = join(__dirname, "..", "..");

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

describe("the /verify page look", () => {
  it("loads site.css, so the page gets the Vakya design instead of the old look", () => {
    const layout = readFileSync(join(SRC, "app", "verify", "layout.tsx"), "utf8");
    expect(layout).toMatch(/import\s+"\.\.\/site\.css"/);
  });

  it("renders inside the .site scope with the heading, the labelled file input and the primary buttons", () => {
    const { container } = render(<VerifyPage />);
    expect(container.querySelector(".site")).not.toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Verify a clip" })).toBeInTheDocument();
    expect(screen.getByLabelText("Audio file to verify")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose a file" })).toHaveClass("s-btn", "s-btn--primary");
    expect(screen.getByRole("link", { name: /Open the desk/ })).toHaveAttribute("href", "/desk");
  });

  it("still checks a clip and shows the verdict", async () => {
    const body: VerifyResponse = {
      manifest: null,
      match: { exact: false, receipt: null, signature_valid: false },
      verdict: "not_detected",
    };
    global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body }) as unknown as typeof fetch;
    render(<VerifyPage />);
    const input = screen.getByLabelText("Audio file to verify");
    fireEvent.change(input, { target: { files: [new File(["x"], "edited.wav", { type: "audio/wav" })] } });
    await waitFor(() => expect(screen.getByTestId("verify-result")).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Not detected" })).toBeInTheDocument();
  });
});

describe("secondary text colour", () => {
  it("--color-text-secondary meets WCAG AA (4.5:1) on white", () => {
    const css = readFileSync(join(SRC, "app", "globals.css"), "utf8");
    const hex = /--color-text-secondary:\s*(#[0-9a-fA-F]{6})/.exec(css)?.[1];
    expect(hex).toBe("#6b6b6b");
    const ratio = 1.05 / (luminance(hex as string) + 0.05);
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });
});
