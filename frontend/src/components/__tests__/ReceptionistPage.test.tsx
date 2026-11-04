import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ReceptionistPage from "@/app/(app)/desk/receptionist/page";
import { toast } from "sonner";
import { useAgents } from "@/hooks/useApi";
import { useDeskTemplate } from "@/components/illustrations/DeskTemplateContext";

let mockParams = new URLSearchParams();
jest.mock("next/navigation", () => ({ useSearchParams: () => mockParams }));
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));
jest.mock("@/lib/api", () => {
  class ApiError extends Error {}
  return { api: { get: jest.fn().mockRejectedValue(new Error("offline")), post: jest.fn(), put: jest.fn(), del: jest.fn() }, ApiError, API_URL: "http://api.test" };
});
jest.mock("@/hooks/useApi", () => ({
  useAgents: jest.fn(),
  useAgentTemplates: () => ({ data: [{ id: "hotel", type_label: "Hotel", persona: { tone: "Courteous", traits: ["a", "b", "c"], sample_line: { en: "x", hi: "y" } } }] }),
  useVoices: () => ({ data: [{ id: "v9", name: "Asha", display_name: "Asha" }] }),
}));
jest.mock("@/components/agents/TemplateGallery", () => ({
  TemplateGallery: (p: { onStartFromScratch?: () => void }) => (
    <div>
      <p>gallery</p>
      <button onClick={p.onStartFromScratch}>scratch card</button>
    </div>
  ),
}));
jest.mock("@/components/agents/CallDialog", () => ({ CallDialog: () => null }));
jest.mock("@/components/voices/VoiceLibrary", () => ({ VoiceLibrary: () => <p>voice library</p> }));
jest.mock("@/components/agents/ConfirmDialog", () => ({ ConfirmDialog: () => null }));
// The page only decides which receptionist to open and on which tab; the editor's own behaviour has its own tests.
jest.mock("@/components/agents/AgentEditor", () => {
  const actual = jest.requireActual("@/components/agents/AgentEditor");
  return {
    ...actual,
    AgentEditor: (p: { agent: { name: string }; focusKnowledge?: boolean; tab?: string; presetVoiceId?: string; onTabChange?: (t: string) => void }) => (
      <div data-testid="editor" data-focus={String(!!p.focusKnowledge)} data-tab={p.tab} data-voice={p.presetVoiceId ?? ""}>
        editing {p.agent.name}
        <MockProbe />
        <button onClick={() => p.onTabChange?.("voice")}>go voice</button>
      </div>
    ),
  };
});

function MockProbe() {
  return <span data-testid="template-type">{useDeskTemplate()?.id ?? "none"}</span>;
}

const mockAgents = useAgents as jest.Mock;
const agent = (id: string, name: string) => ({
  id,
  name,
  greeting: "",
  system_instructions: "",
  supported_languages: ["en"],
  voice_id: null,
  document_count: 0,
  created_at: "2026-10-01T00:00:00Z",
});
const withAgents = (data: unknown) => mockAgents.mockReturnValue({ data, isLoading: false, error: undefined, mutate: jest.fn() });

beforeEach(() => {
  mockParams = new URLSearchParams();
  jest.clearAllMocks();
  window.history.replaceState(null, "", "/desk/receptionist");
});

describe("Receptionist page", () => {
  it("opens the only receptionist straight away, on the Business tab", async () => {
    withAgents([agent("a1", "Ria")]);
    render(<ReceptionistPage />);
    const editor = await screen.findByTestId("editor");
    expect(editor.textContent).toContain("editing Ria");
    expect(editor.getAttribute("data-tab")).toBe("business");
  });

  it("offers the templates and the describe-it form when there is none yet", () => {
    withAgents([]);
    render(<ReceptionistPage />);
    expect(screen.getByText("gallery")).toBeTruthy();
    expect(screen.getByLabelText("Name")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Create front desk/ })).toBeTruthy();
  });

  it("lets a voice be cloned before any receptionist exists (the Clone a voice shortcut)", () => {
    mockParams = new URLSearchParams("tab=voice&new=1");
    withAgents([]);
    render(<ReceptionistPage />);
    expect(screen.getByText("voice library")).toBeTruthy();
  });

  it("does not break several front desks: a picker with icons switches between them", async () => {
    withAgents([{ ...agent("a1", "Ria"), template_id: "clinic" }, { ...agent("a2", "Dev"), template_id: "hotel" }]);
    render(<ReceptionistPage />);
    expect((await screen.findByTestId("editor")).textContent).toContain("editing Ria");
    fireEvent.click(screen.getByRole("button", { name: /Front desk Ria/ }));
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.querySelector("img")?.getAttribute("src"))).toEqual(["/illustrations/clinic-icon.svg", "/illustrations/hotel-icon.svg"]);
    fireEvent.click(options[1]);
    expect((await screen.findByTestId("editor")).textContent).toContain("editing Dev");
    expect(window.location.search).toBe("?agent=a2&tab=business");
  });

  it("hands the open agent's template (persona) to the header, by the type the agent came from", async () => {
    withAgents([{ ...agent("a1", "Ria"), template_id: "hotel" }]);
    render(<ReceptionistPage />);
    expect((await screen.findByTestId("template-type")).textContent).toBe("hotel");
  });

  it("gives no template to a custom agent", async () => {
    withAgents([agent("a1", "Ria")]);
    render(<ReceptionistPage />);
    expect((await screen.findByTestId("template-type")).textContent).toBe("none");
  });

  it("is titled Front desk when none is open, and the scratch card takes you to the describe-it form", () => {
    withAgents([]);
    render(<ReceptionistPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Front desk" })).toBeTruthy();
    const name = screen.getByLabelText("Name") as HTMLInputElement;
    const scroll = jest.fn();
    window.HTMLElement.prototype.scrollIntoView = scroll;
    fireEvent.click(screen.getByRole("button", { name: "scratch card" }));
    expect(scroll).toHaveBeenCalled();
    expect(document.activeElement).toBe(name);
    expect(name.placeholder).not.toMatch(/Sunrise/);
  });

  it("New front desk opens the create form with the templates", async () => {
    withAgents([agent("a1", "Ria")]);
    render(<ReceptionistPage />);
    await screen.findByTestId("editor");
    fireEvent.click(screen.getByRole("button", { name: /New front desk/ }));
    expect(screen.getByLabelText("Name")).toBeTruthy();
    expect(screen.getByText("gallery")).toBeTruthy();
    expect(screen.queryByTestId("editor")).toBeNull();
  });
});

describe("Receptionist deep link (?agent=<id>&tab=...)", () => {
  it("opens that receptionist, set to focus Documents with tab=knowledge", async () => {
    mockParams = new URLSearchParams("agent=a2&tab=knowledge");
    withAgents([agent("a1", "Ria"), agent("a2", "Dev")]);
    render(<ReceptionistPage />);
    const editor = await screen.findByTestId("editor");
    expect(editor.textContent).toContain("editing Dev");
    expect(editor.getAttribute("data-tab")).toBe("documents");
    expect(editor.getAttribute("data-focus")).toBe("true");
  });

  it.each([
    ["business", "business"],
    ["languages", "languages"],
    ["documents", "documents"],
    ["voice", "voice"],
    ["phone", "phone"],
    [null, "business"],
    ["nonsense", "business"],
  ])("tab=%s opens the %s tab", async (param, expectedTab) => {
    mockParams = new URLSearchParams(param ? `agent=a1&tab=${param}` : "agent=a1");
    withAgents([agent("a1", "Ria")]);
    render(<ReceptionistPage />);
    expect((await screen.findByTestId("editor")).getAttribute("data-tab")).toBe(expectedTab);
  });

  it("waits for the receptionist to load, then opens the editor", async () => {
    mockParams = new URLSearchParams("agent=a1");
    mockAgents.mockReturnValue({ data: undefined, isLoading: true, error: undefined, mutate: jest.fn() });
    const { rerender } = render(<ReceptionistPage />);
    expect(screen.queryByTestId("editor")).toBeNull();
    withAgents([agent("a1", "Ria")]);
    rerender(<ReceptionistPage />);
    expect((await screen.findByTestId("editor")).textContent).toContain("editing Ria");
  });

  it("tells the owner when the receptionist no longer exists", async () => {
    mockParams = new URLSearchParams("agent=gone");
    withAgents([agent("a1", "Ria")]);
    render(<ReceptionistPage />);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("That front desk no longer exists."));
  });

  it("changing the tab updates the address so the link can be shared", async () => {
    mockParams = new URLSearchParams("agent=a1&tab=business");
    withAgents([agent("a1", "Ria")]);
    render(<ReceptionistPage />);
    fireEvent.click(await screen.findByText("go voice"));
    expect(window.location.search).toBe("?agent=a1&tab=voice");
    expect(screen.getByTestId("editor").getAttribute("data-tab")).toBe("voice");
  });

  it("hands a voice picked in the voice list (?voice=<id>) to the editor", async () => {
    mockParams = new URLSearchParams("agent=a1&tab=voice&voice=v9");
    withAgents([agent("a1", "Ria")]);
    render(<ReceptionistPage />);
    expect((await screen.findByTestId("editor")).getAttribute("data-voice")).toBe("v9");
  });
});
