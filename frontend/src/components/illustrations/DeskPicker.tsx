"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { DeskIllustration } from "./DeskIllustration";
import { deskType } from "./deskTypes";

export interface DeskOption {
  id: string;
  name: string;
  /** Front-desk type id for the icon ("clinic", ...); anything else draws the custom icon. */
  desk?: string | null;
}

interface Props {
  /** The visible and accessible label, e.g. "Front desk". */
  label: string;
  value: string | null;
  options: DeskOption[];
  onChange: (id: string) => void;
  className?: string;
}

/** A select whose rows carry the front-desk icon (a native select cannot show pictures). */
export function DeskPicker({ label, value, options, onChange, className }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const uid = useId();
  const current = options.find((o) => o.id === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  useEffect(() => {
    if (open) list.current?.focus();
  }, [open]);

  const show = () => {
    setActive(Math.max(0, options.findIndex((o) => o.id === current?.id)));
    setOpen(true);
  };
  const pick = (id: string) => {
    setOpen(false);
    button.current?.focus();
    if (id !== value) onChange(id);
  };
  const onListKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") setActive((a) => Math.min(options.length - 1, a + 1));
    else if (e.key === "ArrowUp") setActive((a) => Math.max(0, a - 1));
    else if (e.key === "Home") setActive(0);
    else if (e.key === "End") setActive(options.length - 1);
    else if (e.key === "Enter" || e.key === " ") pick(options[active].id);
    else if (e.key === "Escape") {
      setOpen(false);
      button.current?.focus();
    } else if (e.key === "Tab") setOpen(false);
    else return;
    e.preventDefault();
  };

  if (!current) return null;
  return (
    <div ref={root} className={cn("relative flex items-center gap-2 text-sm text-text-secondary", className)}>
      <span id={`${uid}-label`}>{label}</span>
      <button
        ref={button}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-labelledby={`${uid}-label ${uid}-value`}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            show();
          }
        }}
        className="inline-flex h-10 min-w-0 max-w-[18rem] items-center gap-2 rounded-lg border border-border bg-bg-surface pl-1.5 pr-2.5 text-sm text-text-primary hover:bg-bg-base focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary"
      >
        <DeskIllustration id={current.desk} variant="icon" size={28} />
        <span id={`${uid}-value`} className="truncate">
          {current.name}
        </span>
        <ChevronDown className={cn("size-4 shrink-0 text-text-secondary transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <ul
          ref={list}
          role="listbox"
          tabIndex={-1}
          aria-labelledby={`${uid}-label`}
          aria-activedescendant={`${uid}-o${active}`}
          onKeyDown={onListKey}
          className="absolute left-0 top-full z-30 mt-1 max-h-72 min-w-64 max-w-[min(24rem,calc(100vw-2rem))] overflow-auto rounded-xl border border-border bg-white p-1 outline-none"
        >
          {options.map((o, i) => (
            <li
              key={o.id}
              id={`${uid}-o${i}`}
              role="option"
              aria-selected={o.id === current.id}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(o.id)}
              className={cn(
                "flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 text-text-primary",
                i === active && "bg-bg-base",
              )}
            >
              <DeskIllustration id={o.desk} variant="icon" size={32} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{o.name}</span>
                <span className="block text-xs text-text-secondary">{deskType(o.desk).label}</span>
              </span>
              {o.id === current.id && <Check className="size-4 shrink-0 text-accent-primary" aria-hidden />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
