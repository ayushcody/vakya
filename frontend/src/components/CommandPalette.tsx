"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { ArrowRight, Bot, CornerDownLeft, Search } from "lucide-react";
import { useAgents } from "@/hooks/useApi";
import { APP_FONT_CLASSES } from "@/lib/appFonts";
import { formatDate } from "@/lib/formatDate";
import { cn } from "@/lib/utils";

export interface PaletteAction {
  id: string;
  label: string;
  /** Small grey text on the right, e.g. "Page" or "Agent". */
  hint: string;
  href: string;
  /** Extra words that should find this action. */
  keywords?: string;
  agent?: boolean;
}

/** Every page of the desk, in the same order and with the same names as the sidebar. */
export const PAGE_ACTIONS: PaletteAction[] = [
  { id: "go-today", label: "Go to Today", hint: "Page", href: "/desk", keywords: "home overview dashboard start bookings" },
  { id: "go-week", label: "Go to This week", hint: "Page", href: "/desk/week", keywords: "report summary insights busiest hours after hours languages missed weekly" },
  { id: "go-receptionist", label: "Go to Front desk", hint: "Page", href: "/desk/receptionist", keywords: "agent set up template hours slots languages documents voice phone number" },
  { id: "go-schedules", label: "Go to Schedules", hint: "Page", href: "/desk/schedules", keywords: "hours slots calendar block busy timings" },
  { id: "go-calls", label: "Go to Calls", hint: "Page", href: "/desk/calls", keywords: "phone transcripts history live monitor takeover" },
  { id: "go-bookings", label: "Go to Bookings", hint: "Page", href: "/desk/bookings", keywords: "appointments booked slots" },
  { id: "go-callers", label: "Go to Callers", hint: "Page", href: "/desk/callers", keywords: "remembered returning profiles forget" },
  { id: "go-try", label: "Go to Try a call", hint: "Page", href: "/desk/try", keywords: "test web call phone call me practice demo" },
  { id: "go-report", label: "Go to Report card", hint: "Page", href: "/desk/report-card", keywords: "acoustic stress noise 8 khz codec task completion accuracy" },
  { id: "go-theatre", label: "Go to Caller Theatre", hint: "Page", href: "/desk/theatre", keywords: "synthetic callers scenarios scorer edge case aadhaar cancellation test" },
  { id: "go-proof", label: "Go to Proof", hint: "Page", href: "/desk/proof", keywords: "evidence report card noise theatre receipt verify judges" },
  { id: "go-settings", label: "Go to Settings", hint: "Page", href: "/desk/settings", keywords: "gnani prisma timbre provider consent privacy phone" },
  { id: "go-verify", label: "Check a voice receipt", hint: "Page", href: "/verify", keywords: "verify receipt clip" },
  { id: "clone", label: "Clone a voice", hint: "Action", href: "/desk/receptionist?tab=voice&new=1", keywords: "new voice record consent" },
  { id: "try-call", label: "Try a call", hint: "Action", href: "/desk/try", keywords: "web call phone me" },
];

/** Case-insensitive match: a substring scores higher the earlier it is; otherwise letters in order ("agnt" finds "Agents"). */
function scoreOne(text: string, token: string): number {
  const at = text.indexOf(token);
  if (at >= 0) return 1000 - at;
  let i = 0;
  for (const ch of text) if (ch === token[i] && ++i === token.length) return 100;
  return -1;
}

/** Every word typed must match the label or its keywords. Returns -1 for no match. */
export function scoreAction(a: PaletteAction, query: string): number {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return 0;
  const label = a.label.toLowerCase();
  const all = `${label} ${a.keywords ?? ""}`.toLowerCase();
  let total = 0;
  for (const t of tokens) {
    const s = Math.max(scoreOne(label, t), scoreOne(all, t) - 200);
    if (s < 0) return -1;
    total += s;
  }
  return total;
}

/** "Mon 12 Oct": tells apart agents that share a name. */
function shortDate(iso?: string): string {
  return formatDate(iso);
}

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  // Mounted only while open, so the agents list is fetched lazily and the form state resets every time.
  if (!open) return null;
  return <PaletteDialog onClose={onClose} />;
}

function PaletteDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const agents = useAgents();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const uid = useId();
  const listId = `${uid}-list`;

  // Remember what had focus so it goes back there on close.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    return () => {
      if (before && document.contains(before)) before.focus();
    };
  }, []);

  const actions = useMemo<PaletteAction[]>(() => {
    const agentHint = (iso?: string) => {
      const d = shortDate(iso);
      return d ? `Front desk, ${d}` : "Front desk";
    };
    const agentActions = (agents.data ?? []).flatMap((a): PaletteAction[] => [
      { id: `try-${a.id}`, label: `Try ${a.name}`, hint: agentHint(a.created_at), href: `/desk/try?agent=${encodeURIComponent(a.id)}`, keywords: "practice call test conversation", agent: true },
      { id: `edit-${a.id}`, label: `Edit ${a.name}`, hint: agentHint(a.created_at), href: `/desk/receptionist?agent=${encodeURIComponent(a.id)}`, keywords: "settings change", agent: true },
    ]);
    return [...PAGE_ACTIONS.slice(0, 1), ...agentActions, ...PAGE_ACTIONS.slice(1)];
  }, [agents.data]);

  const results = useMemo(() => {
    if (!query.trim()) return actions;
    return actions
      .map((a, i) => ({ a, s: scoreAction(a, query), i }))
      .filter((r) => r.s >= 0)
      .sort((x, y) => y.s - x.s || x.i - y.i)
      .map((r) => r.a);
  }, [actions, query]);

  const current = results.length ? Math.min(active, results.length - 1) : -1;

  useEffect(() => {
    if (current < 0) return;
    listRef.current?.children[current]?.scrollIntoView?.({ block: "nearest" });
  }, [current]);

  const run = (a: PaletteAction | undefined) => {
    if (!a) return;
    onClose();
    router.push(a.href);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (results.length) setActive((current + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (results.length) setActive((current - 1 + results.length) % results.length);
    } else if (e.key === "Home" && results.length) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End" && results.length) {
      e.preventDefault();
      setActive(results.length - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(results[current]);
    } else if (e.key === "Tab") {
      // Focus trap: the search box is the only stop in the dialog.
      e.preventDefault();
    }
  };

  const loading = agents.isLoading && !agents.data;

  return createPortal(
    <div className={`fixed inset-0 z-[70] flex items-start justify-center px-3 pt-[8vh] md:pt-[14vh] ${APP_FONT_CLASSES}`} onKeyDown={onKeyDown}>
      <div className="absolute inset-0 bg-black/50" aria-hidden onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search and commands"
        className="relative flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border bg-bg-surface shadow-2xl"
      >
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search className="size-4 shrink-0 text-text-secondary" aria-hidden />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={current >= 0 ? `${uid}-opt-${current}` : undefined}
            aria-autocomplete="list"
            aria-label="Search pages and actions"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            placeholder="Search pages and actions"
            className="h-12 min-w-0 flex-1 bg-transparent text-base text-text-primary outline-none placeholder:text-text-secondary"
          />
          <kbd className="hidden rounded border border-border px-1.5 py-0.5 text-[11px] text-text-secondary md:block">Esc</kbd>
        </div>

        <ul ref={listRef} id={listId} role="listbox" aria-label="Results" className="min-h-0 flex-1 overflow-y-auto p-2">
          {results.map((a, i) => (
            <li
              key={a.id}
              id={`${uid}-opt-${i}`}
              role="option"
              aria-selected={i === current}
              onMouseMove={() => i !== current && setActive(i)}
              onClick={() => run(a)}
              className={cn(
                "flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm text-text-primary",
                i === current ? "bg-bg-elevated" : ""
              )}
            >
              {a.agent ? (
                <Bot className="size-4 shrink-0 text-text-secondary" aria-hidden />
              ) : (
                <ArrowRight className="size-4 shrink-0 text-text-secondary" aria-hidden />
              )}
              <span className="min-w-0 flex-1 truncate">{a.label}</span>
              <span className="shrink-0 text-xs text-text-secondary">{a.hint}</span>
              {i === current && <CornerDownLeft className="hidden size-3.5 shrink-0 text-text-secondary md:block" aria-hidden />}
            </li>
          ))}
        </ul>

        <div role="status" className="border-t border-border px-4 py-2 text-xs text-text-secondary">
          {results.length === 0
            ? `Nothing matches "${query.trim()}".`
            : loading
              ? "Loading your front desk..."
              : `${results.length} ${results.length === 1 ? "result" : "results"}. Use the arrow keys and Enter.`}
        </div>
      </div>
    </div>,
    document.body
  );
}
