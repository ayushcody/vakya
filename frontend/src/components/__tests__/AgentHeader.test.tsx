import { render as rtlRender, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { SWRConfig } from "swr";
import { AgentHeader } from "@/components/agents/AgentHeader";
import { DeskTemplateContext } from "@/components/illustrations/DeskTemplateContext";
import { rememberDeskType } from "@/components/illustrations/deskTypes";
import type { Agent, AgentTemplate } from "@/types/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn().mockRejectedValue(new Error("offline")) } }));

const agent = (over: Partial<Agent> = {}): Agent => ({
  id: "a1",
  name: "Lotus Stay front desk",
  greeting: "Hi",
  system_instructions: "Be kind.",
  supported_languages: ["en"],
  voice_id: null,
  created_at: "2026-01-01T00:00:00Z",
  document_count: 1,
  ...over,
});
const hotel = {
  id: "hotel",
  type_label: "Hotel",
  accent: "amber",
  persona: { tone: "Courteous", traits: ["Polished", "Quick", "Helpful"], sample_line: { en: "Welcome.", hi: "स्वागत है।" } },
} as unknown as AgentTemplate;

const render = (ui: ReactElement, template: AgentTemplate | null = null) =>
  rtlRender(
    <SWRConfig value={{ provider: () => new Map() }}>
      <DeskTemplateContext.Provider value={template}>{ui}</DeskTemplateContext.Provider>
    </SWRConfig>,
  );
const header = (a: Agent) => <AgentHeader agent={a} voiceLabel="Asha" onDelete={jest.fn()} />;

beforeEach(() => window.localStorage.clear());

describe("AgentHeader", () => {
  it("shows the agent's type illustration, the type label and the persona chips", () => {
    render(header(agent({ template_id: "hotel" })), hotel);
    const art = screen.getByTestId("desk-illustration").querySelector("img")!;
    expect(art.getAttribute("src")).toBe("/illustrations/hotel.svg");
    expect(screen.getByTestId("desk-type").textContent).toBe("Hotel front desk");
    const chips = within(screen.getByRole("list", { name: "How it sounds" })).getAllByRole("listitem");
    expect(chips.map((c) => c.textContent)).toEqual(["Courteous", "Polished", "Quick", "Helpful"]);
  });

  it("uses the type this browser remembers when the server did not store one", () => {
    rememberDeskType("a1", "hotel");
    render(header(agent()), hotel);
    expect(screen.getByTestId("desk-illustration").querySelector("img")!.getAttribute("src")).toBe("/illustrations/hotel.svg");
  });

  it("falls back to the custom desk, without a type label or persona chips", () => {
    render(header(agent()), hotel);
    expect(screen.getByTestId("desk-illustration").querySelector("img")!.getAttribute("src")).toBe("/illustrations/custom.svg");
    expect(screen.getByTestId("desk-type").textContent).toBe("Front desk");
    expect(screen.queryByRole("list", { name: "How it sounds" })).toBeNull();
  });

  it("still renders for a typed agent when the templates have not loaded", () => {
    render(header(agent({ template_id: "clinic" })), null);
    expect(screen.getByTestId("desk-type").textContent).toBe("Clinic front desk");
    expect(screen.queryByRole("list", { name: "How it sounds" })).toBeNull();
  });

  it("calls an unnamed one a front desk", () => {
    render(<AgentHeader agent={agent({ name: "" })} name=" " voiceLabel="Asha" onDelete={jest.fn()} />);
    expect(screen.getByTestId("agent-title").textContent).toBe("Unnamed front desk");
  });
});
