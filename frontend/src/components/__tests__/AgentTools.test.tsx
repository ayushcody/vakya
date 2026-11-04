import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AgentEditor } from "@/components/agents/AgentEditor";
import { api } from "@/lib/api";
import type { Agent } from "@/types/api";

jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn(), info: jest.fn() } }));
jest.mock("@/lib/api", () => ({ api: { put: jest.fn().mockResolvedValue({}), get: jest.fn().mockRejectedValue(new Error("offline")) } }));
jest.mock("@/hooks/useApi", () => ({ useAgents: () => ({ mutate: jest.fn().mockResolvedValue(undefined) }) }));
jest.mock("@/components/agents/KnowledgePanel", () => ({ KnowledgePanel: () => null }));
jest.mock("@/components/voices/VoiceLibrary", () => ({ VoiceLibrary: () => null }));

const agent: Agent = {
  id: "a1",
  name: "Reception",
  greeting: "Hello",
  system_instructions: "Be kind.",
  supported_languages: ["en"],
  voice_id: null,
  disclose_ai: true,
  tools: [],
  created_at: "2026-01-01T00:00:00Z",
  document_count: 0,
};

describe("receptionist tools setting", () => {
  it("shows the appointments checkbox, reflecting the receptionist, and saves the chosen tools", async () => {
    render(<AgentEditor agent={agent} voices={[]} tab="business" onDelete={() => undefined} />);
    const box = screen.getByRole("checkbox", { name: /Let the front desk book times/ });
    expect(box).not.toBeChecked();
    expect(screen.getByRole("button", { name: /Save front desk/ })).toBeDisabled();

    fireEvent.click(box);
    expect(box).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: /Save front desk/ }));
    await waitFor(() => expect(api.put).toHaveBeenCalled());
    expect((api.put as jest.Mock).mock.calls[0][0]).toBe("/api/agents/a1");
    expect((api.put as jest.Mock).mock.calls[0][1]).toMatchObject({ tools: ["schedules"] });
  });

  it("starts checked when the receptionist already has the tool", () => {
    render(<AgentEditor agent={{ ...agent, tools: ["appointments"] }} voices={[]} tab="business" onDelete={() => undefined} />);
    expect(screen.getByRole("checkbox", { name: /Let the front desk book times/ })).toBeChecked();
  });
});

describe("the older appointments tool", () => {
  it("shows as checked, and switching the box off removes it while on adds the schedules tool", async () => {
    render(<AgentEditor agent={{ ...agent, tools: ["appointments"] }} voices={[]} tab="business" onDelete={() => undefined} />);
    const box = screen.getByRole("checkbox", { name: /Let the front desk book times/ });
    expect(box).toBeChecked();
    fireEvent.click(box);
    expect(box).not.toBeChecked();
    fireEvent.click(box);
    fireEvent.click(screen.getByRole("button", { name: /Save front desk/ }));
    await waitFor(() => expect(api.put).toHaveBeenCalled());
    expect((api.put as jest.Mock).mock.calls.at(-1)[1]).toMatchObject({ tools: ["schedules"] });
  });

  it("a front desk made from a template, which carries schedules, starts checked", () => {
    render(<AgentEditor agent={{ ...agent, tools: ["schedules"] }} voices={[]} tab="business" onDelete={() => undefined} />);
    expect(screen.getByRole("checkbox", { name: /Let the front desk book times/ })).toBeChecked();
  });
});

describe("the editor is generic", () => {
  it("names no invented business and no clinic-only wording", () => {
    render(<AgentEditor agent={agent} voices={[]} tab="business" onDelete={() => undefined} />);
    expect(screen.getByRole("tablist", { name: "Front desk settings" })).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    const placeholders = [...document.querySelectorAll("[placeholder]")].map((e) => e.getAttribute("placeholder")).join(" ");
    expect(text + placeholders).not.toMatch(/Sunrise|Joshi|Bhosale|receptionist|clinic or office/i);
  });
});
