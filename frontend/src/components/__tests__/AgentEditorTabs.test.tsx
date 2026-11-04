import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { SWRConfig } from "swr";
import { AGENT_TABS, AgentEditor, parseTabParam } from "@/components/agents/AgentEditor";
import { api } from "@/lib/api";
import type { Agent } from "@/types/api";

jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() } }));
jest.mock("@/lib/api", () => ({ api: { put: jest.fn().mockResolvedValue({}), get: jest.fn() } }));
jest.mock("@/hooks/useApi", () => ({ useAgents: () => ({ mutate: jest.fn().mockResolvedValue(undefined) }) }));
jest.mock("@/components/agents/KnowledgePanel", () => ({ KnowledgePanel: () => <p>documents panel</p> }));
jest.mock("@/components/voices/VoiceLibrary", () => ({ VoiceLibrary: () => <p>voice library</p> }));

const agent: Agent = {
  id: "a1",
  name: "Reception",
  greeting: "Namaste",
  system_instructions: "Be kind.",
  supported_languages: ["en"],
  voice_id: null,
  disclose_ai: true,
  tools: [],
  created_at: "2026-01-01T00:00:00Z",
  document_count: 0,
};
// A fresh SWR cache per render, so the status lookups of one test never leak into the next.
const wrap = (ui: ReactElement) => <SWRConfig value={{ provider: () => new Map() }}>{ui}</SWRConfig>;
const render = (ui: ReactElement) => {
  const r = rtlRender(wrap(ui));
  return { ...r, rerender: (next: ReactElement) => r.rerender(wrap(next)) };
};
const noop = () => undefined;
const props = { agent, voices: [], onDelete: noop };

beforeEach(() => {
  jest.clearAllMocks();
  (api.get as jest.Mock).mockRejectedValue(new Error("offline"));
});

describe("parseTabParam", () => {
  it("accepts the five tabs", () => {
    for (const t of AGENT_TABS) expect(parseTabParam(t.id)).toEqual({ tab: t.id, focusKnowledge: false });
  });
  it("keeps the knowledge link working: Documents, focused. No tab opens Business without scrolling", () => {
    expect(parseTabParam("knowledge")).toEqual({ tab: "documents", focusKnowledge: true });
    expect(parseTabParam(null)).toEqual({ tab: "business", focusKnowledge: false });
  });
  it("falls back to Business for anything else", () => {
    expect(parseTabParam("billing")).toEqual({ tab: "business", focusKnowledge: false });
  });
});

describe("AgentEditor tabs", () => {
  it("opens on Business with name, greeting and the business details, and nothing from the other tabs", () => {
    render(<AgentEditor {...props} />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Business", "Languages", "Documents", "Voice", "Phone number"]);
    expect(screen.getByRole("tab", { name: "Business" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("Name")).toHaveValue("Reception");
    expect(screen.getByLabelText("Greeting")).toBeInTheDocument();
    expect(screen.getByLabelText("Business details, hours and slots")).toHaveValue("Be kind.");
    expect(screen.queryByText("documents panel")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Voice" })).toBeNull();
  });

  it("switches tabs by click and keeps unsaved edits across tabs", () => {
    const onTabChange = jest.fn();
    render(<AgentEditor {...props} onTabChange={onTabChange} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Front desk" } });
    fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
    expect(onTabChange).toHaveBeenCalledWith("documents");
    expect(screen.getByText("documents panel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Business" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Front desk");
  });

  it("shows the tab it is told to (deep link): Voice has the voice box and the voice library", () => {
    const { rerender } = render(<AgentEditor {...props} tab="voice" />);
    expect(screen.getByRole("tab", { name: "Voice" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("combobox", { name: "Voice" })).toBeInTheDocument();
    expect(screen.getByText("voice library")).toBeInTheDocument();
    rerender(<AgentEditor {...props} tab="phone" />);
    expect(screen.getByRole("heading", { name: "Phone number" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/desk/settings");
  });

  it("books appointments is a tool on Business, with no Approvals link anywhere", () => {
    render(<AgentEditor {...props} />);
    expect(screen.getByLabelText(/Let the front desk book times/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Approvals/ })).toBeNull();
  });

  it("moves between tabs with the arrow keys", () => {
    render(<AgentEditor {...props} />);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Business" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Languages" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Languages" }), { key: "End" });
    expect(screen.getByRole("tab", { name: "Phone number" })).toHaveAttribute("aria-selected", "true");
  });

  it("still scrolls to and focuses Documents for the Add-a-document link", async () => {
    render(<AgentEditor {...props} tab="documents" focusKnowledge />);
    await waitFor(() => expect(document.activeElement?.id).toBe("agent-knowledge"));
  });
});

describe("AgentEditor header", () => {
  it("has Try a call (to the Try page with this receptionist) and Phone someone when given", () => {
    const onPhone = jest.fn();
    render(<AgentEditor {...props} onPhone={onPhone} />);
    expect(screen.getByRole("link", { name: "Try a call" })).toHaveAttribute("href", "/desk/try?agent=a1");
    fireEvent.click(screen.getByRole("button", { name: "Phone someone" }));
    expect(onPhone).toHaveBeenCalledWith(agent);
  });

  it("keeps Delete in the ... menu", () => {
    const onDelete = jest.fn();
    render(<AgentEditor {...props} onDelete={onDelete} />);
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete front desk" }));
    expect(onDelete).toHaveBeenCalledWith(agent);
  });

  it("counts only real checks: business, documents, voice, phone, tried once", async () => {
    (api.get as jest.Mock).mockImplementation((path: string) =>
      Promise.resolve(path.startsWith("/api/calls") ? { calls: [{ id: "c1" }] } : { configured: true })
    );
    render(<AgentEditor {...props} agent={{ ...agent, document_count: 2, voice_id: "v1" }} voices={[{ id: "v1", name: "Asha" } as never]} />);
    // the business text, documents and voice are known at once; the phone line and the call history arrive from the server
    await screen.findByText("5 of 5");
    expect(screen.getByText("Phone connected")).toBeInTheDocument();
    expect(screen.getByText("Ready to answer")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("/api/calls?days=365&limit=1&agent_id=a1"));
  });

  it("starts at 1 of 5 for a bare receptionist, and says what is missing", async () => {
    render(<AgentEditor {...props} />);
    expect(screen.getByText("1 of 5")).toBeInTheDocument();
    expect(screen.getByText("Needs documents")).toBeInTheDocument();
    expect(screen.getByText(/Add your documents/)).toBeInTheDocument();
    expect(screen.getByText(/Connect your phone line/)).toBeInTheDocument();
  });
});

describe("AgentEditor saving", () => {
  it("says why Save is disabled: nothing changed, then no name", () => {
    render(<AgentEditor {...props} />);
    const save = screen.getByRole("button", { name: /Save front desk/ });
    expect(save).toBeDisabled();
    expect(screen.getAllByText("No changes to save yet").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  " } });
    expect(save).toBeDisabled();
    expect(screen.getAllByText("Add a name first").length).toBeGreaterThan(0);
  });

  it("picks languages by name and saves the same codes as before", async () => {
    render(<AgentEditor {...props} tab="languages" />);
    expect(screen.getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Hindi" }));
    expect(screen.getByRole("button", { name: "Hindi" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /Save front desk/ }));
    await waitFor(() => expect(api.put).toHaveBeenCalled());
    expect((api.put as jest.Mock).mock.calls[0][1]).toMatchObject({ supported_languages: ["en", "hi"] });
  });

  it("keeps a language code it does not know instead of dropping it", () => {
    render(<AgentEditor {...props} agent={{ ...agent, supported_languages: ["en", "xx"] }} tab="languages" />);
    expect(screen.getByRole("button", { name: "XX" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("AgentEditor voice from the voice list", () => {
  it("preselects the voice as an unsaved change and saves it", async () => {
    render(<AgentEditor {...props} tab="voice" voices={[{ id: "v9", name: "Asha" } as never]} presetVoiceId="v9" />);
    expect(screen.getByRole("combobox", { name: "Voice" })).toHaveValue("v9");
    expect(screen.getByText("Voice chosen from the list below. Save to use it.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Save front desk/ }));
    await waitFor(() => expect(api.put).toHaveBeenCalled());
    expect((api.put as jest.Mock).mock.calls[0][1]).toMatchObject({ voice_id: "v9" });
  });
});
