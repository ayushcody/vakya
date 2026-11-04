"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Bot, ChevronDown, Plus, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useAgents, useAgentTemplates, useVoices } from "@/hooks/useApi";
import { PageShell } from "@/components/ui/PageHeader";
import { toView } from "@/components/voices/voiceApi";
import { Button, Card, EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import {
  AgentEditor,
  parseTabParam,
  fieldClass as input,
  labelClass as label,
  type AgentTab,
} from "@/components/agents/AgentEditor";
import { ConfirmDialog } from "@/components/agents/ConfirmDialog";
import { VoiceLibrary } from "@/components/voices/VoiceLibrary";
import { TemplateGallery } from "@/components/agents/TemplateGallery";
import { CallDialog } from "@/components/agents/CallDialog";
import { errMessage } from "@/components/desk/format";
import { LLM_FIX_TEXT } from "@/lib/llm";
import { DeskPicker } from "@/components/illustrations/DeskPicker";
import { DeskTemplateContext } from "@/components/illustrations/DeskTemplateContext";
import { deskKeyOfAgent } from "@/components/illustrations/deskTypes";
import type { Agent, AgentTemplate } from "@/types/api";

/** Keep `?agent=<id>&tab=<tab>` in the address bar without adding history entries (Back still leaves the page). */
function writeUrl(agentId: string | null, tab: AgentTab = "business") {
  if (typeof window === "undefined") return;
  const qs = agentId ? `?agent=${encodeURIComponent(agentId)}&tab=${tab}` : "";
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${qs}`);
}

export default function ReceptionistPage() {
  // useSearchParams needs a Suspense boundary for static rendering
  return (
    <Suspense fallback={null}>
      <Receptionist />
    </Suspense>
  );
}

function Receptionist() {
  const agents = useAgents();
  const params = useSearchParams();
  const deepLinkAgent = params?.get("agent") ?? null;
  const deepLinkTab = params?.get("tab") ?? null;
  const voiceParam = params?.get("voice") ?? null;
  const voices = useVoices();
  const templates = useAgentTemplates();
  const voiceViews = useMemo(() => (voices.data ?? []).map(toView), [voices.data]);

  // A voice sent from the voice list ("Use for the receptionist"): waits here until the editor is open.
  const [pendingVoice, setPendingVoice] = useState<string | null>(null);
  useEffect(() => {
    if (voiceParam) setPendingVoice(voiceParam);
  }, [voiceParam]);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [tab, setTab] = useState<AgentTab>("business");
  const [focusKnowledge, setFocusKnowledge] = useState(false);
  const handledLink = useRef<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [roleDesc, setRoleDesc] = useState("");
  const [agentName, setAgentName] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Agent | null>(null);
  const [calling, setCalling] = useState<Agent | null>(null);
  const [callOpen, setCallOpen] = useState(false);
  const openCall = (a: Agent) => {
    setCalling(a);
    setCallOpen(true);
  };
  const [tryAsking, setTryAsking] = useState<{ agentId: string; questions: string[] } | null>(null);

  /** Open one receptionist (optionally on a tab) and put it in the address bar so the link can be shared or reloaded. */
  const openAgent = (id: string, nextTab: AgentTab = "business", focus = false) => {
    handledLink.current = id;
    setCreating(false);
    setEditingId(id);
    setTab(nextTab);
    setFocusKnowledge(focus);
    writeUrl(id, nextTab);
  };

  const formRef = useRef<HTMLDivElement>(null);
  /** "Start from scratch" card: take the owner to the describe-it form below the gallery. */
  const startFromScratch = () => {
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    document.getElementById("agent-name")?.focus({ preventScroll: true });
  };

  const handleTemplateCreated = (agent: Agent, template: AgentTemplate) => {
    setTryAsking({ agentId: agent.id, questions: template.suggested_questions });
    openAgent(agent.id);
  };

  // /desk/receptionist?agent=<id>&tab=business|languages|documents|voice|phone opens that receptionist on that tab. The
  // older `tab=knowledge` opens Documents. Handled once per link. Without a link the first receptionist opens: one is the
  // common case.
  useEffect(() => {
    if (!agents.data) return;
    if (!deepLinkAgent) {
      if (editingId === null && agents.data.length > 0 && handledLink.current === null) {
        handledLink.current = agents.data[0].id;
        const parsed = parseTabParam(deepLinkTab);
        setEditingId(agents.data[0].id);
        setTab(parsed.tab);
        setFocusKnowledge(parsed.focusKnowledge);
      }
      return;
    }
    if (handledLink.current === deepLinkAgent) return;
    handledLink.current = deepLinkAgent;
    if (agents.data.some((a) => a.id === deepLinkAgent)) {
      const parsed = parseTabParam(deepLinkTab);
      setCreating(false);
      setEditingId(deepLinkAgent);
      setTab(parsed.tab);
      setFocusKnowledge(parsed.focusKnowledge);
    } else {
      toast.error("That front desk no longer exists.");
    }
  }, [deepLinkAgent, deepLinkTab, agents.data, editingId]);

  // `?tab=voice&voice=<id>` without an agent: open the first one on that tab.
  const pendingVoiceName = pendingVoice ? voiceViews.find((v) => v.id === pendingVoice)?.name : undefined;
  const editing = agents.data?.find((a) => a.id === editingId) ?? null;
  const hasAgents = !!agents.data?.length;
  const deskTemplate = useMemo(() => {
    const key = deskKeyOfAgent(editing);
    return key === "custom" ? null : ((templates.data ?? []).find((t) => t.id === key) ?? null);
  }, [editing, templates.data]);

  const handleGenerate = async () => {
    if (!roleDesc.trim() || !agentName.trim()) return;
    setIsGenerating(true);
    setLlmError(null);
    try {
      const created = await api.post<Agent>("/api/agents", {
        name: agentName.trim(),
        role_description: roleDesc.trim(),
      });
      await agents.mutate();
      setAgentName("");
      setRoleDesc("");
      openAgent(created.id);
      toast.success("Front desk created. Review it and add your documents.");
    } catch (e) {
      if (e instanceof ApiError && e.code === "LLM_UNAVAILABLE") setLlmError(e.message);
      else toast.error(errMessage(e, "Could not create the front desk. Try again."));
    } finally {
      setIsGenerating(false);
    }
  };

  const handleDelete = async () => {
    if (!toDelete) return;
    try {
      await api.del(`/api/agents/${toDelete.id}`);
      const wasOpen = editingId === toDelete.id;
      if (wasOpen) {
        setPendingVoice(null);
        setEditingId(null);
        setFocusKnowledge(false);
        handledLink.current = "closed";
        writeUrl(null);
      }
      setToDelete(null);
      const next = await agents.mutate();
      if (wasOpen && next && next.length > 0) openAgent(next[0].id);
      toast.success("Front desk deleted.");
    } catch (e) {
      toast.error(errMessage(e, "Could not delete the front desk."));
    }
  };

  const createBlocker = !agentName.trim()
    ? "Add a name first"
    : !roleDesc.trim()
      ? "Describe what the front desk should do first"
      : "";

  const createForm = (
    <Card className="space-y-5">
      <div className="border-b border-border pb-4">
        <h2 className="flex items-center gap-2 font-display text-lg font-semibold text-text-primary">
          <Sparkles className="size-4 text-accent-primary" aria-hidden />
          Describe your own front desk
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          Say in a sentence or two what your front desk should do, and we will write its instructions for you. You can change them later.
        </p>
      </div>
      <div>
        <label htmlFor="agent-name" className={label}>
          Name
        </label>
        <input
          id="agent-name"
          className={input}
          value={agentName}
          onChange={(e) => setAgentName(e.target.value)}
          placeholder="e.g. Front desk for my repair shop"
        />
      </div>
      <div>
        <label htmlFor="agent-role" className={label}>
          What should it do?
        </label>
        <textarea
          id="agent-role"
          rows={3}
          className={`${input} resize-none`}
          value={roleDesc}
          onChange={(e) => setRoleDesc(e.target.value)}
          placeholder="e.g. Answers calls for a small business, shares timings and prices, and books visits politely in Hindi, Marathi and English."
        />
      </div>
      {llmError && (
        <div role="alert" className="rounded-xl border border-accent-warning/30 bg-accent-warning/10 p-4 text-sm">
          <p className="font-medium text-text-primary">Setup needed</p>
          <p className="mt-1 text-text-secondary">
            Vakya cannot write the front desk yet because no language model is connected. Ask the person who installed Vakya to set one up.
          </p>
          <details className="group mt-2">
            <summary className="inline-flex min-h-10 cursor-pointer list-none items-center gap-1 text-xs font-medium text-text-secondary hover:text-text-primary">
              <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" aria-hidden /> Details
            </summary>
            <p className="mt-1 text-xs text-text-secondary">{llmError}</p>
            <p className="mt-1 text-xs text-text-secondary">
              Fix: <code className="rounded bg-bg-elevated px-1.5 py-0.5 font-mono">{LLM_FIX_TEXT}</code>
            </p>
          </details>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-end gap-3">
        {createBlocker && <p className="text-xs text-text-secondary">{createBlocker}</p>}
        {hasAgents && (
          <Button variant="ghost" onClick={() => setCreating(false)} disabled={isGenerating}>
            Cancel
          </Button>
        )}
        <Button
          onClick={handleGenerate}
          loading={isGenerating}
          disabled={!!createBlocker}
          disabledReason={createBlocker || undefined}
          leftIcon={<Bot className="size-4" />}
        >
          {isGenerating ? "Creating" : "Create front desk"}
        </Button>
      </div>
    </Card>
  );

  let body: React.ReactNode;
  if (agents.isLoading) {
    body = (
      <Card className="space-y-4">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
      </Card>
    );
  } else if (agents.error) {
    body = <ErrorState title="Could not load your front desk" message={agents.error.message} onRetry={() => agents.mutate()} />;
  } else if (!hasAgents) {
    body = (
      <>
        <EmptyState
          icon={<Bot />}
          title="No front desk yet"
          description="Pick a kind of front desk below, such as a clinic or a hotel, or describe your own in a sentence."
        />
        <TemplateGallery onCreated={handleTemplateCreated} onStartFromScratch={startFromScratch} />
        <div ref={formRef} className="scroll-mt-6">
          {createForm}
        </div>
        {/* A voice can be cloned before the receptionist exists (the "Clone a voice" shortcut lands here), then picked later. */}
        {deepLinkTab === "voice" && (
          <Card>
            <VoiceLibrary />
          </Card>
        )}
      </>
    );
  } else if (creating) {
    body = (
      <>
        <TemplateGallery onCreated={handleTemplateCreated} onStartFromScratch={startFromScratch} />
        <div ref={formRef} className="scroll-mt-6">
          {createForm}
        </div>
      </>
    );
  } else if (editing) {
    const hint =
      tryAsking?.agentId === editing.id && tryAsking.questions.length > 0 ? tryAsking.questions : null;
    body = (
      <DeskTemplateContext.Provider value={deskTemplate}>
        {hint && (
          <Card variant="solid" className="rounded-xl border-accent-primary/30 bg-accent-primary/5">
            <p className="text-sm font-medium text-text-primary">Try asking your front desk:</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-text-secondary">
              {hint.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
          </Card>
        )}
        <AgentEditor
          key={editing.id}
          agent={editing}
          voices={voiceViews}
          focusKnowledge={focusKnowledge}
          tab={tab}
          onTabChange={(t) => {
            setTab(t);
            setFocusKnowledge(false);
            writeUrl(editing.id, t);
          }}
          presetVoiceId={pendingVoiceName ? (pendingVoice ?? undefined) : undefined}
          onPhone={openCall}
          onDelete={setToDelete}
        />
      </DeskTemplateContext.Provider>
    );
  } else {
    body = <Skeleton className="h-64 w-full rounded-2xl" />;
  }

  const several = (agents.data?.length ?? 0) > 1;

  return (
    <PageShell width="wide" className="space-y-6">
      {!editing && (
        <PageHeader
          title="Front desk"
          description="The agent that answers your phone: what it knows, how it sounds and where it picks up."
        />
      )}
      {(several || (hasAgents && !creating)) && (
        <div className="flex flex-wrap items-center gap-3">
          {several && (
            <DeskPicker
              label="Front desk"
              value={editingId}
              options={agents.data!.map((a) => ({ id: a.id, name: a.name, desk: deskKeyOfAgent(a) }))}
              onChange={(id) => openAgent(id, tab)}
            />
          )}
          {hasAgents && !creating && (
            <Button variant="outline" size="sm" onClick={() => setCreating(true)} leftIcon={<Plus className="size-4" />}>
              New front desk
            </Button>
          )}
        </div>
      )}
      {body}
      <CallDialog agent={calling} open={callOpen} onClose={() => setCallOpen(false)} />
      <ConfirmDialog
        open={!!toDelete}
        title={toDelete ? `Delete ${toDelete.name}?` : "Delete front desk"}
        description="This removes the front desk and its documents. This cannot be undone."
        confirmLabel="Delete front desk"
        keepLabel="Keep front desk"
        onCancel={() => setToDelete(null)}
        onConfirm={handleDelete}
      />
    </PageShell>
  );
}
