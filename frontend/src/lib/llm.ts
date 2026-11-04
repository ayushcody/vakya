/** Shown wherever no language model is configured (the Today page, the receptionist page and Settings). */
export const LLM_FIX_TEXT = "Add GEMINI_API_KEY to backend/.env and restart the backend.";

const PROVIDER_LABEL: Record<string, string> = { gemini: "Gemini Flash" };

/** Short label for the language model provider. */
export function llmLabel(provider?: string | null): string {
  if (!provider) return "No language model";
  return PROVIDER_LABEL[provider] ?? provider;
}
