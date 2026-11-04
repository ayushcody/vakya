"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { CalendarCheck, Save } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { toast } from "sonner";
import { Button, Card } from "@/components/ui";
import { api } from "@/lib/api";
import { useAgents } from "@/hooks/useApi";
import { errMessage } from "@/components/desk/format";
import type { Agent, AgentUpdate } from "@/types/api";
import type { VoiceView } from "@/components/voices/voiceApi";
import { VoiceLibrary } from "@/components/voices/VoiceLibrary";
import { AgentHeader, SetupChecklist } from "./AgentHeader";
import { KnowledgePanel } from "./KnowledgePanel";
import { LanguagePicker } from "./LanguagePicker";
import { PhoneTab } from "./PhoneTab";
import type { AgentTab } from "./agentStatus";

export type { AgentTab };

export const fieldClass =
  "w-full rounded-lg border border-border bg-bg-primary px-3 py-2.5 text-sm text-text-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary";
export const labelClass =
  "mb-1.5 block text-sm font-medium text-text-secondary";

export const AGENT_TABS: { id: AgentTab; label: string }[] = [
  { id: "business", label: "Business" },
  { id: "languages", label: "Languages" },
  { id: "documents", label: "Documents" },
  { id: "voice", label: "Voice" },
  { id: "phone", label: "Phone number" },
];

/**
 * Tool servers the front desk can use (the backend knows them by these names). Front desks made from a template carry
 * "schedules"; the older doctor-list "appointments" is still read, and switching the box off removes both.
 */
export const TOOL_OPTIONS: { id: string; also?: string[]; label: string; hint: string; details: string }[] = [
  {
    id: "schedules",
    also: ["appointments"],
    label: "Let the front desk book times",
    hint: "It checks the schedule, offers the nearest free times, books and cancels while it talks to someone, and reads every date and time back before it commits.",
    details: "This turns on the \"schedules\" tool server, which runs on this computer. Its bookings appear on the Bookings page, and a front desk that has schedules gets it anyway.",
  },
];

/**
 * `?tab=` of the front desk page. `knowledge` (the older name, still used by "Add a document" links) opens Documents and
 * focuses it. No tab, or anything unknown, opens Business.
 */
export function parseTabParam(raw: string | null | undefined): {
  tab: AgentTab;
  focusKnowledge: boolean;
} {
  if (raw === "knowledge") return { tab: "documents", focusKnowledge: true };
  const hit = AGENT_TABS.find((t) => t.id === raw);
  return { tab: hit ? hit.id : "business", focusKnowledge: false };
}

const parseLangs = (raw: string[]) => raw.map((s) => s.trim()).filter(Boolean);

const check =
  "mt-0.5 size-5 shrink-0 accent-[var(--color-accent)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary";

interface Props {
  agent: Agent;
  voices: VoiceView[];
  onDelete: (agent: Agent) => void;
  /** Focus the Documents panel on open (the "Add a document" links). */
  focusKnowledge?: boolean;
  /** Which tab is showing. Leave out and the editor keeps its own (starts on Business). */
  tab?: AgentTab;
  onTabChange?: (tab: AgentTab) => void;
  /** Open the Call dialog ("Phone someone", and the setup for incoming calls). The buttons only show when this is given. */
  onPhone?: (agent: Agent) => void;
  /** A voice picked in the voice list ("Use for the receptionist"): preselected in the Voice box as an unsaved change. */
  presetVoiceId?: string;
}

/** One receptionist: header with its main actions, then Business / Languages / Documents / Voice / Phone number. Re-mount (key by id) to reset the draft. */
export function AgentEditor({
  agent,
  voices,
  onDelete,
  focusKnowledge,
  tab: tabProp,
  onTabChange,
  onPhone,
  presetVoiceId,
}: Props) {
  const { mutate } = useAgents();
  const knowledgeRef = useRef<HTMLDivElement>(null);
  const tab0 = tabProp ?? "business";
  useEffect(() => {
    if (!focusKnowledge || tab0 !== "documents") return;
    knowledgeRef.current?.scrollIntoView?.({
      block: "start",
      behavior: "smooth",
    });
    knowledgeRef.current?.focus({ preventScroll: true });
  }, [focusKnowledge, tab0]);

  const reduce = useReducedMotion();
  const [innerTab, setInnerTab] = useState<AgentTab>("business");
  const tab = tabProp ?? innerTab;
  const setTab = (t: AgentTab) => {
    setInnerTab(t);
    onTabChange?.(t);
  };

  const [name, setName] = useState(agent.name);
  const [greeting, setGreeting] = useState(agent.greeting);
  const [instructions, setInstructions] = useState(agent.system_instructions);
  const [langs, setLangs] = useState<string[]>(agent.supported_languages);
  const [voiceId, setVoiceId] = useState(presetVoiceId ?? agent.voice_id ?? "");
  useEffect(() => {
    if (presetVoiceId) setVoiceId(presetVoiceId);
  }, [presetVoiceId]);
  const [disclose, setDisclose] = useState(agent.disclose_ai !== false);
  const [tools, setTools] = useState<string[]>(agent.tools ?? []);
  const [saving, setSaving] = useState(false);

  const parsedLangs = parseLangs(langs);
  const dirty =
    name !== agent.name ||
    greeting !== agent.greeting ||
    instructions !== agent.system_instructions ||
    parsedLangs.join(",") !== agent.supported_languages.join(",") ||
    (voiceId || null) !== (agent.voice_id ?? null) ||
    disclose !== (agent.disclose_ai !== false) ||
    [...tools].sort().join(",") !== [...(agent.tools ?? [])].sort().join(",");

  const nameMissing = !name.trim();
  const saveBlocker = nameMissing
    ? "Add a name first"
    : !dirty
      ? "No changes to save yet"
      : "";

  const save = async () => {
    if (saveBlocker) return;
    const body: AgentUpdate = {
      name: name.trim(),
      greeting,
      system_instructions: instructions,
      supported_languages: parsedLangs.length ? parsedLangs : ["en"],
      voice_id: voiceId || null,
      disclose_ai: disclose,
      tools,
    };
    setSaving(true);
    try {
      await api.put<Agent>(`/api/agents/${agent.id}`, body);
      await mutate();
      toast.success("Front desk saved.");
    } catch (e) {
      toast.error(errMessage(e, "Could not save the front desk. Try again."));
    } finally {
      setSaving(false);
    }
  };

  const voiceMissing = voiceId && !voices.some((v) => v.id === voiceId);
  const voiceLabel = agent.voice_id
    ? (voices.find((v) => v.id === agent.voice_id)?.name ?? "Unknown voice")
    : "Default voice";

  const saveBar = (
    <div className="flex flex-wrap items-center justify-end gap-3 border-t border-border pt-4">
      {saveBlocker ? (
        <p className="text-xs text-text-secondary" id="save-reason">
          {saveBlocker}
        </p>
      ) : (
        <p className="text-xs text-warning-text" role="status">
          You have unsaved changes
        </p>
      )}
      <Button
        onClick={save}
        loading={saving}
        disabled={!!saveBlocker}
        disabledReason={saveBlocker || undefined}
        leftIcon={<Save className="size-4" />}
      >
        {saving ? "Saving" : "Save front desk"}
      </Button>
    </div>
  );

  const onTabKeys = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = AGENT_TABS.findIndex((t) => t.id === tab);
    let next = i;
    if (e.key === "ArrowRight" || e.key === "ArrowDown")
      next = (i + 1) % AGENT_TABS.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
      next = (i - 1 + AGENT_TABS.length) % AGENT_TABS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = AGENT_TABS.length - 1;
    else return;
    e.preventDefault();
    setTab(AGENT_TABS[next].id);
    document.getElementById(`agent-tab-${AGENT_TABS[next].id}`)?.focus();
  };

  return (
    <div className="space-y-5">
      <AgentHeader
        agent={agent}
        name={name}
        voiceLabel={voiceLabel}
        onDelete={onDelete}
        onPhone={onPhone}
      />

      <div className="space-y-5 lg:grid lg:grid-cols-[13rem_minmax(0,1fr)] lg:items-start lg:gap-6 lg:space-y-0">
        <div className="space-y-4 lg:sticky lg:top-24">
          <div
            role="tablist"
            aria-label="Front desk settings"
            onKeyDown={onTabKeys}
            className="-mx-4 flex gap-1 overflow-x-auto border-b border-border px-4 sm:mx-0 sm:px-0 lg:mx-0 lg:flex-col lg:overflow-visible lg:border-b-0 lg:px-0"
          >
            {AGENT_TABS.map((t) => {
              const on = t.id === tab;
              return (
                <button
                  key={t.id}
                  id={`agent-tab-${t.id}`}
                  type="button"
                  role="tab"
                  aria-selected={on}
                  aria-controls={`agent-panel-${t.id}`}
                  tabIndex={on ? 0 : -1}
                  onClick={() => setTab(t.id)}
                  className={`relative isolate -mb-px min-h-11 shrink-0 whitespace-nowrap border-b-2 px-4 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent-primary lg:mb-0 lg:w-full lg:justify-start lg:rounded-lg lg:border-b-0 lg:px-3 lg:py-2 lg:text-left ${
                    on
                      ? "border-accent-primary text-text-primary"
                      : "border-transparent text-text-secondary hover:text-text-primary"
                  }`}
                >
                  {on && (
                    <motion.span
                      layoutId="agent-tab-thumb"
                      aria-hidden
                      className="absolute inset-0 -z-10 hidden rounded-lg bg-bg-elevated lg:block"
                      transition={
                        reduce
                          ? { duration: 0 }
                          : { type: "spring", stiffness: 400, damping: 32 }
                      }
                    />
                  )}
                  {t.label}
                </button>
              );
            })}
          </div>
          <SetupChecklist agent={agent} onGoToTab={setTab} />
        </div>

        <div
          role="tabpanel"
          id={`agent-panel-${tab}`}
          aria-labelledby={`agent-tab-${tab}`}
        >
          <motion.div
            key={tab}
            className="space-y-6"
            initial={reduce ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          >
            {tab === "business" && (
              <Card className="space-y-5">
                <div className="border-b border-border pb-4">
                  <h2 className="font-display text-lg font-semibold text-text-primary">
                    The business
                  </h2>
                  <p className="mt-1 text-sm text-text-secondary">
                    What the front desk is called, what it says first, and what it needs to know about your business.
                  </p>
                </div>
                <div>
                  <label htmlFor="agent-name-edit" className={labelClass}>
                    Name
                  </label>
                  <input
                    id="agent-name-edit"
                    className={fieldClass}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    aria-invalid={nameMissing || undefined}
                  />
                  {nameMissing && (
                    <p className="mt-1.5 text-xs text-accent-error">
                      Add a name first.
                    </p>
                  )}
                </div>
                <div>
                  <label htmlFor="agent-greet" className={labelClass}>
                    Greeting
                  </label>
                  <input
                    id="agent-greet"
                    className={fieldClass}
                    value={greeting}
                    onChange={(e) => setGreeting(e.target.value)}
                  />
                  <p className="mt-1.5 text-xs text-text-secondary">
                    The first thing it says. Example: Namaste, this is your
                    business name. How can I help?
                  </p>
                </div>
                <div>
                  <label htmlFor="agent-sys" className={labelClass}>
                    Business details, hours and slots
                  </label>
                  <textarea
                    id="agent-sys"
                    className={`${fieldClass} h-48 resize-y leading-relaxed`}
                    value={instructions}
                    onChange={(e) => setInstructions(e.target.value)}
                    placeholder="Example: You answer the phone for your business in your city. Open Monday to Saturday, 9 am to 6 pm, 15-minute slots. Be polite and short. Never promise anything you cannot check."
                  />
                  <p className="mt-1.5 text-xs text-text-secondary">
                    Written instructions it follows on every call: the business name, address, opening hours, how long a slot is, and what it must never do. Fees, staff and rules are better as documents.
                  </p>
                </div>
                {TOOL_OPTIONS.map((t) => (
                  <div key={t.id}>
                    <label
                      htmlFor={`agent-tool-${t.id}`}
                      className="flex cursor-pointer items-start gap-3"
                    >
                      <input
                        id={`agent-tool-${t.id}`}
                        type="checkbox"
                        checked={[t.id, ...(t.also ?? [])].some((x) => tools.includes(x))}
                        onChange={(e) =>
                          setTools(
                            e.target.checked
                              ? [...tools.filter((x) => !(t.also ?? []).includes(x)), t.id]
                              : tools.filter((x) => x !== t.id && !(t.also ?? []).includes(x)),
                          )
                        }
                        className={check}
                      />
                      <span>
                        <span className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
                          <CalendarCheck className="size-4" aria-hidden />
                          {t.label}
                        </span>
                        <span className="mt-0.5 block text-xs text-text-secondary">
                          {t.hint}
                        </span>
                      </span>
                    </label>
                    <details className="group mt-2 pl-8">
                      <summary className="inline-flex min-h-10 cursor-pointer list-none items-center gap-1 text-xs font-medium text-text-secondary hover:text-text-primary">
                        Details
                      </summary>
                      <p className="mt-1 text-xs text-text-secondary">
                        {t.details}
                      </p>
                    </details>
                  </div>
                ))}
                <div>
                  <label
                    htmlFor="agent-disclose"
                    className="flex cursor-pointer items-start gap-3"
                  >
                    <input
                      id="agent-disclose"
                      type="checkbox"
                      role="switch"
                      checked={disclose}
                      onChange={(e) => setDisclose(e.target.checked)}
                      className={check}
                    />
                    <span>
                      <span className="block text-sm font-medium text-text-primary">
                        Say it&apos;s an AI at the start of each call
                      </span>
                      <span className="mt-0.5 block text-xs text-text-secondary">
                        The first reply of every conversation begins with a
                        short spoken line saying this is an AI assistant.
                      </span>
                    </span>
                  </label>
                </div>
                {saveBar}
              </Card>
            )}

            {tab === "languages" && (
              <Card className="space-y-5">
                <div className="border-b border-border pb-4">
                  <h2 className="font-display text-lg font-semibold text-text-primary">
                    Languages
                  </h2>
                  <p className="mt-1 text-sm text-text-secondary">
                    The front desk listens in all of these and answers in the language the caller starts in. Mixed
                    speech such as Hindi with English words is understood.
                  </p>
                </div>
                <div>
                  <p id="agent-langs-label" className={labelClass}>
                    Languages
                  </p>
                  <LanguagePicker value={langs} onChange={setLangs} />
                  <p className="mt-1.5 text-xs text-text-secondary">
                    English is used if you pick none.
                  </p>
                </div>
                {saveBar}
              </Card>
            )}

            {tab === "documents" && (
              <div
                ref={knowledgeRef}
                id="agent-knowledge"
                tabIndex={-1}
                aria-label="Documents"
                className="scroll-mt-32 rounded-xl outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary"
              >
                <KnowledgePanel agentId={agent.id} />
              </div>
            )}

            {tab === "voice" && (
              <>
                <Card className="space-y-5">
                  <div className="border-b border-border pb-4">
                    <h2 className="font-display text-lg font-semibold text-text-primary">
                      Which voice answers
                    </h2>
                    <p className="mt-1 text-sm text-text-secondary">
                      The voice callers hear. Use a preset, or clone a voice below with the speaker&apos;s consent.
                    </p>
                  </div>
                  <div>
                    <label htmlFor="agent-voice" className={labelClass}>
                      Voice
                    </label>
                    <select
                      id="agent-voice"
                      className={fieldClass}
                      value={voiceId}
                      onChange={(e) => setVoiceId(e.target.value)}
                    >
                      <option value="">
                        {voices.length
                          ? "Default voice"
                          : "No voices yet (uses default)"}
                      </option>
                      {voiceMissing && (
                        <option value={voiceId}>{voiceId} (not found)</option>
                      )}
                      {voices.map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.name}
                        </option>
                      ))}
                    </select>
                    {presetVoiceId &&
                      voiceId === presetVoiceId &&
                      voiceId !== (agent.voice_id ?? "") && (
                        <p
                          role="status"
                          className="mt-1.5 text-xs font-medium text-warning-text"
                        >
                          Voice chosen from the list below. Save to use it.
                        </p>
                      )}
                  </div>
                  {saveBar}
                </Card>
                <Card>
                  <VoiceLibrary />
                </Card>
              </>
            )}

            {tab === "phone" && <PhoneTab agent={agent} onPhone={onPhone} />}
          </motion.div>
        </div>
      </div>
    </div>
  );
}
