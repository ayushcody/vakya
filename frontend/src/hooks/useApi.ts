"use client";

import { useMemo } from "react";
import useSWR, { SWRConfiguration } from "swr";
import { api, ApiError } from "@/lib/api";
import type {
  Voice,
  HealthStatus,
  EngineInfo,
  ConsentMode,
  Agent,
  AgentDocument,
  AgentTemplate,
} from "@/types/api";

function useGet<T>(path: string | null, config?: SWRConfiguration<T, ApiError>) {
  return useSWR<T, ApiError>(path, (p: string) => api.get<T>(p), {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
    ...config,
  });
}

/** GET a list endpoint whose body is either a bare array or `{ [key]: T[] }`. */
function useList<T>(path: string, key: string, config?: SWRConfiguration<T[], ApiError>) {
  return useSWR<T[], ApiError>(
    path,
    async (p: string) => {
      const body = await api.get<T[] | Record<string, T[]>>(p);
      if (Array.isArray(body)) return body;
      return (body?.[key] as T[] | undefined) ?? [];
    },
    { revalidateOnFocus: false, shouldRetryOnError: false, ...config }
  );
}

/** Backend voice rows carry `display_name`; the shared Voice type (and every picker) reads `name`. */
const withVoiceName = (v: Voice): Voice => {
  const displayName = (v as Record<string, unknown>).display_name;
  return v.name ? v : { ...v, name: typeof displayName === "string" && displayName ? displayName : "Untitled voice" };
};

export const useVoices = () => {
  const res = useList<Voice>("/api/voices", "voices");
  const raw = res.data;
  const data = useMemo(() => (raw?.some((v) => !v.name) ? raw.map(withVoiceName) : raw), [raw]);
  return { ...res, data };
};
export const useHealth = () => useGet<HealthStatus>("/api/health", { refreshInterval: 30000 });
/** The speech engines this server offers (GET /api/engines), with `available` and why not for Gnani's hosted ones. */
export const useEngines = () => useGet<EngineInfo[]>("/api/engines", { revalidateOnFocus: false });
/** How consent is collected when a voice is cloned (GET /api/consent/mode). */
export const useConsentMode = () => useGet<ConsentMode>("/api/consent/mode", { revalidateOnFocus: false });

export const AGENTS_KEY = "/api/agents";
export const useAgents = () => useList<Agent>(AGENTS_KEY, "agents");
export const useAgentDocuments = (id: string | null | undefined) =>
  useSWR<AgentDocument[], ApiError>(
    id ? `/api/agents/${id}/documents` : null,
    async (p: string) => (await api.get<{ documents?: AgentDocument[] }>(p))?.documents ?? [],
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );
/** Questions the user can ask this agent, written from its documents (re-read when the document count changes). */
export const useExampleQuestions = (id: string | null | undefined, documentCount = 0) =>
  useSWR<string[], ApiError>(
    id && documentCount > 0 ? [`/api/agents/${id}/example-questions`, documentCount] : null,
    async ([p]: [string, number]) => (await api.get<{ questions?: string[] }>(p))?.questions ?? [],
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );
export const useAgentTemplates = () =>
  useList<AgentTemplate>("/api/agent-templates", "templates", { revalidateOnFocus: false, revalidateIfStale: false });
