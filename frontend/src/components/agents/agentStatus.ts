"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { Agent, PhoneStatus } from "@/types/api";

const quiet = { revalidateOnFocus: false, shouldRetryOnError: false } as const;

/**
 * Is a phone line set up on this computer? `undefined` while loading or when the answer is unknown.
 * It is account-wide (one provider line), not per agent.
 */
export function usePhoneConnected(): boolean | undefined {
  const { data } = useSWR<boolean>("/api/phone/status", async (p: string) => !!(await api.get<PhoneStatus>(p)).configured, quiet);
  return data;
}

/** Has anyone (a real caller or an owner trying it out) called this receptionist? `undefined` while unknown. */
export function useAgentTalkedTo(agentId: string | null | undefined): boolean | undefined {
  const { data } = useSWR<boolean>(
    agentId ? `/api/calls?days=365&limit=1&agent_id=${encodeURIComponent(agentId)}` : null,
    async (p: string) => ((await api.get<{ calls?: unknown[] }>(p))?.calls?.length ?? 0) > 0,
    quiet
  );
  return data;
}

/** The tabs of the receptionist page, in the order a person sets things up. */
export type AgentTab = "business" | "languages" | "documents" | "voice" | "phone";

export interface SetupStep {
  id: "business" | "documents" | "voice" | "phone" | "talked";
  label: string;
  done: boolean;
  /** Where to go to finish it: a tab of the receptionist page, or the Try page. */
  tab: AgentTab | "try";
}

/** The five real checks behind "Setup 3 of 5". Unknown answers (still loading, or the check failed) count as not done. */
export function setupSteps(agent: Agent, facts: { talkedTo?: boolean; phoneConnected?: boolean }): SetupStep[] {
  return [
    { id: "business", label: "Describe the business and its hours", done: agent.system_instructions.trim().length > 0, tab: "business" },
    { id: "documents", label: "Add your documents", done: agent.document_count > 0, tab: "documents" },
    { id: "voice", label: "Choose a voice", done: !!agent.voice_id, tab: "voice" },
    { id: "phone", label: "Connect your phone line", done: !!facts.phoneConnected, tab: "phone" },
    { id: "talked", label: "Try a call once", done: !!facts.talkedTo, tab: "try" },
  ];
}

/** Ready to answer questions: it has documents to answer from. */
export const agentIsReady = (agent: Agent): boolean => agent.document_count > 0;

/** Link to the Try page with this receptionist selected (the page reads `?agent=`). */
export const tryHref = (agentId: string): string => `/desk/try?agent=${encodeURIComponent(agentId)}`;
