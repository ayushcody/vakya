import { render, screen, within } from "@testing-library/react";
import { RigCheck } from "@/components/site/RigCheck";
import { COMMANDS, KEYS, PORTS } from "@/components/site/rigCheckData";

describe("Run it page content", () => {
  it("lists the five real commands in order", () => {
    render(<RigCheck />);
    expect(COMMANDS).toHaveLength(5);
    const cmds = COMMANDS.map((c) => c.cmd);
    expect(cmds).toEqual(expect.arrayContaining(["./vakya setup", "./vakya start", "./vakya doctor"]));
    const list = screen.getAllByRole("list")[0];
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("./vakya setup")).toBeInTheDocument();
  });

  it("names the ports and the keys, and what happens without each key", () => {
    render(<RigCheck />);
    expect(PORTS.map((p) => p.port)).toEqual(["3010", "8020", "8021"]);
    const table = screen.getByRole("table", { name: /Environment settings in backend\/\.env/ });
    for (const k of ["GNANI_API_KEY", "LLM_API_KEY", "EMBEDDINGS_API_KEY", "VOBIZ_AUTH_ID", "GOOGLE_CALENDAR_ID"]) expect(within(table).getByText(new RegExp(k))).toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(KEYS.length + 1);
    expect(table).toHaveTextContent(/no second speech model/i);
    expect(table).toHaveTextContent(/keyword search/);
  });

  it("says Gnani is not yet live-tested, claims one machine only, and needs no model download", () => {
    const { container } = render(<RigCheck />);
    expect(container.textContent).toMatch(/have not run against the live API yet/);
    expect(container.textContent).toMatch(/Apple M4 with 16 GB/);
    expect(container.textContent).toMatch(/nothing is downloaded/);
    expect(container.textContent).not.toMatch(/Full studio|download a model|GPU/i);
  });

  it("does not link to the private repository", () => {
    const { container } = render(<RigCheck />);
    expect(Array.from(container.querySelectorAll("a")).filter((a) => a.getAttribute("href")?.includes("github.com"))).toEqual([]);
  });
});
