import { render, screen, within } from "@testing-library/react";
import { ReportCard } from "@/components/report/ReportCard";
import { CONDITIONS, STRESS_RUN, type StressRun } from "@/components/report/stressRun";
import nextConfig from "../../../next.config";

const SAMPLE: StressRun = {
  date: "2026-12-01",
  model: "sample-model",
  tasks: 24,
  rows: [
    { condition: "clean", language: "all", keyterms: false, n: 24, taskCompletion: 70, wer: 0.2, latencyS: 0.5, hitByKind: { name: 90, date: 80, time: 90, phone: 10 } },
    { condition: "clean", language: "all", keyterms: true, n: 24, taskCompletion: 80, wer: 0.18, latencyS: 0.5, hitByKind: {} },
    { condition: "noise_0db", language: "all", keyterms: false, n: 24, taskCompletion: 20, wer: 0.6, latencyS: 0.6, hitByKind: { name: 60 } },
  ],
};

describe("report card results file", () => {
  it("is empty until Gnani Prisma has been run, so no number is published", () => {
    expect(STRESS_RUN).toBeNull();
  });
});

describe("ReportCard with no run", () => {
  it("says the Gnani Prisma run is pending, before any table", () => {
    render(<ReportCard />);
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Gnani Prisma run pending");
    expect(note).toHaveTextContent("synthetic speech");
    expect(note).toHaveTextContent("No recogniser has been measured yet");
  });

  it("explains the method: calls, line, score and keyterms", () => {
    render(<ReportCard />);
    const method = screen.getByRole("heading", { name: "How it is measured" }).closest("section")!;
    for (const t of ["The calls", "The line", "The score", "With and without keyterms"]) expect(within(method).getByText(t)).toBeInTheDocument();
    expect(method).toHaveTextContent("8 kHz");
    expect(method).toHaveTextContent("G.711");
    expect(method).toHaveTextContent("street noise at 10, 5 and 0 dB");
  });

  it("prints every condition with an empty, labelled cell and no percentage anywhere", () => {
    const { container } = render(<ReportCard />);
    const table = screen.getByRole("table", { name: /Task completion by condition/ });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(CONDITIONS.length);
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Condition", "No keyterms", "With keyterms", "Word error", "Median time"]);
    expect(within(table).getAllByText("pending")).toHaveLength(CONDITIONS.length * 4);
    expect(container.textContent).not.toMatch(/\d+%/);
  });

  it("lists the fields that will show which part of the booking was lost", () => {
    render(<ReportCard />);
    const table = screen.getByRole("table", { name: /Share of each field found/ });
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Condition", "Name", "Date", "Time", "Amount", "Phone number"]);
  });
});

describe("ReportCard with a run", () => {
  it("fills the table from the results file and names the recogniser", () => {
    render(<ReportCard run={SAMPLE} />);
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Gnani Prisma");
    expect(note).toHaveTextContent("sample-model");
    expect(note).not.toHaveTextContent("run pending");
    const table = screen.getByRole("table", { name: /Task completion by condition/ });
    const clean = within(table).getAllByRole("row")[1];
    expect(clean).toHaveTextContent("70%");
    expect(clean).toHaveTextContent("80%");
    expect(clean).toHaveTextContent("0.20");
    const loud = within(table).getAllByRole("row").find((r) => within(r).queryByText("Street noise, 0 dB"))!;
    expect(loud).toHaveTextContent("20%");
  });

  it("shows a field with no clips as n/a, not zero or pending", () => {
    render(<ReportCard run={SAMPLE} />);
    const table = screen.getByRole("table", { name: /Share of each field found/ });
    const noise = within(table).getAllByRole("row").find((r) => within(r).queryByText("Street noise, 0 dB"))!;
    expect(noise).toHaveTextContent("60%");
    expect(noise).toHaveTextContent("n/a");
  });
});

describe("next.config redirects", () => {
  it("sends /dashboard to the desk and /proof to the report card, and nothing else", async () => {
    const redirects = await nextConfig.redirects!();
    expect(redirects).toEqual([
      { source: "/dashboard", destination: "/desk", permanent: true },
      { source: "/proof", destination: "/report-card", permanent: true },
    ]);
  });
});
