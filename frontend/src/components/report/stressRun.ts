/**
 * The acoustic-stress report card: types and the loader for its results file.
 *
 * Nothing is measured yet. `reportCardResults.json` holds `{ "run": null }` until Gnani Prisma has been run on the task
 * set, and the page then shows "Gnani Prisma run pending" with an empty table. When the run exists, replace the file's
 * contents with the run in the shape below and the pages fill themselves in; no component changes. The method, and the
 * command that produces the numbers, are in docs/STRESS_REPORT.md.
 */
import results from "./reportCardResults.json";

export type StressCondition = "clean" | "tel8k" | "g711" | "noise_10db" | "noise_5db" | "noise_0db" | "tel8k+noise_5db";
export type StressLanguage = "all" | "hi" | "mr" | "hi-en";
export type EntityKind = "name" | "date" | "time" | "amount" | "phone";

export interface StressRow {
  condition: StressCondition;
  language: StressLanguage;
  /** True when the row was recognised with keyterms from the business's documents, false for the plain run. */
  keyterms: boolean;
  /** Clips in this row. */
  n: number;
  /** Share of clips where name AND date AND time were all found, in percent. */
  taskCompletion: number;
  /** Folded word error rate (spelling-tolerant). */
  wer: number;
  /** Median recognition time of one clip, in seconds. */
  latencyS: number;
  /** Share found per entity kind, in percent (amount and phone only count clips that have them). */
  hitByKind: Partial<Record<EntityKind, number>>;
}

export interface StressRun {
  /** ISO date of the run. */
  date: string;
  /** The recogniser's name as the API reports it, for example "prisma-v3.0". */
  model: string;
  /** Clips per condition. */
  tasks: number;
  commit?: string;
  rows: StressRow[];
}

export const STRESS_RUN: StressRun | null = (results as { run: StressRun | null }).run;

export const CONDITIONS: { id: StressCondition; name: string; what: string }[] = [
  { id: "clean", name: "Clean", what: "Unprocessed synthetic speech, resampled to 16 kHz" },
  { id: "tel8k", name: "Phone line, 8 kHz", what: "Band-passed 300 to 3400 Hz and carried at 8 kHz" },
  { id: "g711", name: "Phone codec, G.711", what: "Mu-law at 8 kHz, as on a landline or SIP trunk" },
  { id: "noise_10db", name: "Street noise, 10 dB", what: "Babble and street noise, speech 10 dB above" },
  { id: "noise_5db", name: "Street noise, 5 dB", what: "Speech 5 dB above the noise" },
  { id: "noise_0db", name: "Street noise, 0 dB", what: "Speech and noise equally loud" },
  { id: "tel8k+noise_5db", name: "Phone line and noise", what: "5 dB noise first, then the 8 kHz phone band" },
];

export function findRow(run: StressRun | null, condition: StressCondition, keyterms: boolean, language: StressLanguage = "all"): StressRow | undefined {
  return run?.rows.find((r) => r.condition === condition && r.language === language && r.keyterms === keyterms);
}
