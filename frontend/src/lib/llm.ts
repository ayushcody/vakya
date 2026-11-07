/** Shown wherever no language model is configured (the Today page, the receptionist page and Settings). */
export const LLM_FIX_TEXT = "Add a language model key to backend/.env (see backend/.env.example) and restart the backend.";


/** Short label for the language model provider. */
export function llmLabel(provider?: string | null): string {
  if (!provider) return "No language model";
  return "Hosted language model";
}
