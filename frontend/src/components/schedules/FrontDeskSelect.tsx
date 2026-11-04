"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";
import type { Agent } from "@/types/api";
import { templateIdOf } from "@/lib/schedules";
import { cn } from "@/lib/utils";
import { DeskIcon } from "./DeskIcon";

export const ALL_DESKS = "all";

interface Option {
  value: string;
  label: string;
  templateId: string | null;
}

/**
 * "Bookings for [front desk]": a select whose options carry the front desk's own icon, which a native select cannot show.
 * Arrow keys move, Enter or Space chooses, Escape closes.
 */
export function FrontDeskSelect({
  agents,
  value,
  onChange,
  allowAll = true,
  label = "Front desk",
  size = "title",
  className,
}: {
  agents: Agent[];
  value: string;
  onChange: (value: string) => void;
  allowAll?: boolean;
  label?: string;
  size?: "title" | "field";
  className?: string;
}) {
  const options: Option[] = [
    ...(allowAll ? [{ value: ALL_DESKS, label: "All front desks", templateId: null }] : []),
    ...agents.map((a) => ({ value: a.id, label: a.name, templateId: templateIdOf(a) })),
  ];
  const selected = options.find((o) => o.value === value) ?? options[0];
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const openList = () => {
    setActive(Math.max(0, options.findIndex((o) => o.value === selected?.value)));
    setOpen(true);
  };
  const choose = (o: Option) => {
    onChange(o.value);
    setOpen(false);
    root.current?.querySelector("button")?.focus();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(options.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Home") setActive(0);
    else if (e.key === "End") setActive(options.length - 1);
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (options[active]) choose(options[active]);
    } else if (e.key === "Escape" || e.key === "Tab") {
      setOpen(false);
      if (e.key === "Escape") root.current?.querySelector("button")?.focus();
    }
  };

  const title = size === "title";
  return (
    <div ref={root} className={cn("relative inline-block max-w-full", className)}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-label={`${label}: ${selected?.label ?? "none"}`}
        onClick={() => (open ? setOpen(false) : openList())}
        className={cn(
          "inline-flex max-w-full items-center gap-2 border border-border bg-bg-surface text-left transition-colors hover:bg-bg-base",
          title ? "min-h-11 rounded-xl px-3 font-display text-[clamp(1.25rem,2vw,1.75rem)] leading-tight text-text-primary" : "h-10 min-h-10 rounded-lg px-3 text-sm text-text-primary"
        )}
      >
        <DeskIcon templateId={selected?.templateId} all={selected?.value === ALL_DESKS} size={title ? 28 : 22} />
        <span className="min-w-0 truncate">{selected?.label ?? "No front desks yet"}</span>
        <ChevronDown aria-hidden className={cn("size-4 shrink-0 text-text-secondary transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ul
          ref={list}
          id={`${id}-list`}
          role="listbox"
          tabIndex={-1}
          aria-label={label}
          aria-activedescendant={`${id}-opt-${active}`}
          onKeyDown={onKey}
          className="absolute left-0 top-full z-40 mt-2 max-h-72 min-w-[16rem] max-w-[min(24rem,calc(100vw-2rem))] overflow-auto rounded-xl border border-border bg-bg-surface p-1 shadow-[0_12px_32px_-12px_rgba(0,0,0,0.25)] outline-none"
        >
          {options.map((o, i) => (
            <li
              key={o.value}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={o.value === selected?.value}
              onMouseEnter={() => setActive(i)}
              onClick={() => choose(o)}
              className={cn(
                "flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 text-[15px] text-text-primary",
                i === active && "bg-bg-base"
              )}
            >
              <DeskIcon templateId={o.templateId} all={o.value === ALL_DESKS} size={24} />
              <span className="min-w-0 flex-1 truncate">{o.label}</span>
              {o.value === selected?.value && <Check aria-hidden className="size-4 text-accent-primary" />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
