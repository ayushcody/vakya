import useSWR, { mutate } from "swr";
import { api, ApiError } from "@/lib/api";
import type { HearingSummary, TeachResponse } from "./types";

export const hearingKey = (agentId: string) => `/api/hearing/${encodeURIComponent(agentId)}`;

export function useHearing(agentId: string | null | undefined) {
  return useSWR<HearingSummary, ApiError>(agentId ? hearingKey(agentId) : null, (p: string) => api.get<HearingSummary>(p), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  });
}

export interface TeachInput {
  heard: string;
  meant: string;
  conversation_id?: string | null;
  turn_index?: number | null;
  replace_rule_id?: string;
  evict_oldest_unused?: boolean;
}

export async function teach(agentId: string, input: TeachInput): Promise<TeachResponse> {
  const body: Record<string, unknown> = { heard: input.heard.trim(), meant: input.meant.trim() };
  if (input.conversation_id) body.conversation_id = input.conversation_id;
  if (input.conversation_id && typeof input.turn_index === "number") body.turn_index = input.turn_index;
  if (input.replace_rule_id) body.replace_rule_id = input.replace_rule_id;
  if (input.evict_oldest_unused) body.evict_oldest_unused = true;
  const res = await api.post<TeachResponse>(`${hearingKey(agentId)}/corrections`, body);
  void mutate(hearingKey(agentId));
  return res;
}

export async function deleteRule(agentId: string, ruleId: string): Promise<void> {
  await api.del(`${hearingKey(agentId)}/rules/${encodeURIComponent(ruleId)}`);
  void mutate(hearingKey(agentId));
}

export async function addTerm(agentId: string, term: string): Promise<void> {
  await api.post(`${hearingKey(agentId)}/vocabulary`, { term: term.trim() });
  void mutate(hearingKey(agentId));
}

export async function deleteTerm(agentId: string, term: string): Promise<void> {
  await api.del(`${hearingKey(agentId)}/vocabulary?term=${encodeURIComponent(term)}`);
  void mutate(hearingKey(agentId));
}

/** What went wrong, in the server's own words when it gave them. */
export function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.message ? e.message : fallback;
}

/** The oldest unused correction a RULES_FULL answer names (`details.info.oldest_unused`), if any. */
export function oldestUnused(e: unknown): { id: string; heard: string; meant: string } | null {
  if (!(e instanceof ApiError) || e.code !== "RULES_FULL") return null;
  const info = (e.details as { info?: { oldest_unused?: { id: string; heard: string; meant: string } | null } } | null)?.info;
  return info?.oldest_unused ?? null;
}
