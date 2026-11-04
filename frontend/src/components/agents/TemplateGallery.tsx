"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { CalendarCheck, Check, Loader2, TriangleAlert, UploadCloud, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useAgentTemplates, useAgents } from "@/hooks/useApi";
import { Button, Skeleton } from "@/components/ui";
import { Modal } from "@/components/voices/Modal";
import { errMessage } from "@/components/desk/format";
import { DeskIllustration } from "@/components/illustrations/DeskIllustration";
import { ACCENTS, deskType, rememberDeskType, sampleStandIns } from "@/components/illustrations/deskTypes";
import type { Agent, AgentTemplate, CreatedScheduleRef } from "@/types/api";

/** Languages offered in the form (the template's own languages are always offered too). */
const LANGUAGE_NAMES: Record<string, string> = {
  en: "English",
  hi: "Hindi",
  mr: "Marathi",
  ta: "Tamil",
  te: "Telugu",
  bn: "Bengali",
  gu: "Gujarati",
  kn: "Kannada",
  ml: "Malayalam",
  pa: "Punjabi",
  ur: "Urdu",
  or: "Odia",
};
const MORE_LANGUAGES = Object.keys(LANGUAGE_NAMES);
export const languageName = (code: string) =>
  LANGUAGE_NAMES[code] ?? code.toUpperCase();

/** What the server adds to GET /api/agent-templates for the form (older servers may not send it). */
export type TemplateInfo = AgentTemplate & {
  audience?: string;
  org_label?: string;
  org_placeholder?: string;
  personalise?: {
    name_pattern: string;
    sample_orgs: string[];
    sample_city?: string | null;
    strip: string[];
  };
};

async function downloadSamplePdf(t: TemplateInfo) {
  if (!t.sample_pdf) return;
  try {
    const blob = await api.blob(t.sample_pdf);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download =
      t.sample_pdf
        .split("/")
        .pop()
        ?.replace(/^sample/, t.id) || `${t.id}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    toast.error(errMessage(e, "Could not download the sample PDF."));
  }
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Fill the {org} and {city} placeholders (a missing city drops "in {city}" with it). */
export function fillPlaceholders(text: string, org: string, city?: string): string {
  const c = (city ?? "").trim();
  const withCity = c
    ? text.split("{city}").join(c)
    : text.replace(/\s*(?:\bin\b|\bat\b|,)?\s*\{city\}/gi, "");
  return withCity.split("{org}").join(org);
}

/** What the agent will be called and how it greets, for the typed organisation (placeholders, or the old sample-name swap). */
export function personaFor(
  t: TemplateInfo,
  org: string,
  city?: string,
): { name: string; greeting: string } {
  const p = t.personalise;
  if (!p)
    return {
      name: fillPlaceholders(t.name, org, city).slice(0, 120),
      greeting: fillPlaceholders(t.greeting, org, city),
    };
  let greeting = t.greeting;
  for (const phrase of p.strip) greeting = greeting.split(phrase).join("");
  const names = [...new Set(p.sample_orgs)]
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  if (names.length)
    greeting = greeting.replace(
      new RegExp(names.map(escapeRe).join("|"), "g"),
      () => org,
    );
  return {
    name: p.name_pattern.split("{org}").join(org).slice(0, 120),
    greeting: fillPlaceholders(greeting, org, city),
  };
}

/** The clinic is the default front desk and leads the gallery; the others keep the server's order. */
export const DEFAULT_TEMPLATE = "clinic";
export const templateLabel = (t: TemplateInfo) => t.type_label ?? deskType(t.id).label;
export const bookingFirst = <T extends { id: string }>(list: T[]): T[] =>
  [...list].sort((a, b) => Number(b.id === DEFAULT_TEMPLATE) - Number(a.id === DEFAULT_TEMPLATE));

const DOC_OK = /\.(pdf|txt|md)$/i;
const fieldClass =
  "min-h-11 w-full rounded-lg border border-border bg-bg-primary px-3 py-2.5 text-sm text-text-primary placeholder:text-text-secondary/60 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary";
const labelClass = "mb-1.5 block text-sm font-medium text-text-primary";

/** POST /api/agents/from-template returns the agent, and the schedules created with it (older servers: none). */
type CreatedAgent = Agent & { schedules?: CreatedScheduleRef[] };

interface Props {
  /** Called with the new agent, the template it came from and the schedules created with it, after the agents list is refreshed. */
  onCreated: (agent: Agent, template: AgentTemplate, schedules?: CreatedScheduleRef[]) => void;
  /** Shows the "Start from scratch" card; called when it is chosen (the page then reveals its describe-it form). */
  onStartFromScratch?: () => void;
}

interface FormProps {
  template: TemplateInfo;
  existing: Agent[];
  onCancel: () => void;
  onCreated: Props["onCreated"];
  onBusy: (busy: boolean) => void;
  refresh: () => Promise<unknown>;
  /** Called when the success step is showing (the modal then finishes on close as well as on the button). */
  onDone: (finish: () => void) => void;
}

const scheduleLine = (s: CreatedScheduleRef): string => {
  const parts: string[] = [];
  if (s.kind === "nights") parts.push(s.capacity ? `Nightly, ${s.capacity} rooms` : "Nightly");
  else if (s.slot_minutes) parts.push(`Every ${s.slot_minutes} min`);
  if (s.summary?.hours_text) parts.push(s.summary.hours_text);
  return parts.join(". ");
};

/** How long creating a front desk may take before the dialog gives up and offers a retry.. */
export const CREATE_TIMEOUT_MS = 180_000;

/** Shown in place of the form while the server makes the front desk: a moving bar, the seconds so far and a plain reason. */
function CreatingProgress({ accent, label }: { accent: string; label: string }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const step =
    seconds < 4
      ? "Creating the front desk and its schedules."
      : seconds < 20
        ? "Reading the sample document so the front desk can answer from it."
        : "Still loading the language model. This only happens the first time.";
  return (
    <div role="status" aria-live="polite" className="space-y-4 py-2" data-testid="desk-creating">
      <div className="flex items-start gap-3">
        <Loader2 className="mt-0.5 size-5 shrink-0 animate-spin text-accent-primary" aria-hidden />
        <div className="min-w-0">
          <p className="font-medium text-text-primary">Setting up your front desk</p>
          <p className="mt-1 text-sm text-text-secondary">
            The first one takes up to a minute while the language model loads.
          </p>
        </div>
      </div>
      <div className="relative h-2 overflow-hidden rounded-full" style={{ background: accent }} aria-hidden>
        <div className="progress-slide absolute inset-y-0 w-1/3 rounded-full bg-accent-primary" />
      </div>
      <p className="flex items-center justify-between gap-3 text-xs text-text-secondary">
        <span>{step}</span>
        <span className="shrink-0 tabular-nums">
          {label} {seconds} s
        </span>
      </p>
    </div>
  );
}

function TemplateForm({
  template: t,
  existing,
  onCancel,
  onCreated,
  onBusy,
  refresh,
  onDone,
}: FormProps) {
  const type = deskType(t.id);
  const [org, setOrg] = useState("");
  const [city, setCity] = useState("");
  const [details, setDetails] = useState("");
  const [langs, setLangs] = useState<string[]>(t.languages);
  const [file, setFile] = useState<File | null>(null);
  const [withSample, setWithSample] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dupe, setDupe] = useState<Agent | null>(null);
  const [touched, setTouched] = useState(false);
  const [made, setMade] = useState<{ agent: CreatedAgent; schedules: CreatedScheduleRef[] } | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const orgLabel = type.nameLabel;
  const orgClean = org.trim();
  const choices = useMemo(
    () => [...new Set([...t.languages, ...MORE_LANGUAGES])],
    [t.languages],
  );
  const preview = personaFor(t, orgClean || `[${orgLabel}]`, city);
  const orgMissing = touched && !orgClean;
  const langsMissing = langs.length === 0;

  const toggleLang = (code: string) =>
    setLangs((cur) =>
      cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code],
    );

  const create = async () => {
    setBusy(true);
    setTimedOut(false);
    onBusy(true);
    const ctl = new AbortController();
    let gaveUp = false;
    const timer = setTimeout(() => {
      gaveUp = true;
      ctl.abort();
    }, CREATE_TIMEOUT_MS);
    try {
      const created = await api.post<CreatedAgent>(
        "/api/agents/from-template",
        {
          template_id: t.id,
          org_name: orgClean,
          city: city.trim() || undefined,
          details: details.trim() || undefined,
          languages: langs,
          include_sample: file ? false : withSample,
        },
        { signal: ctl.signal },
      );
      clearTimeout(timer);
      rememberDeskType(created.id, t.id);
      await refresh();
      toast.success(
        file || !withSample
          ? `${created.name} created.`
          : `${created.name} created with a sample document you can replace.`,
      );
      if (file) {
        try {
          const fd = new FormData();
          fd.append("agent_id", created.id);
          fd.append("file", file);
          await api.upload(`/api/knowledge/upload`, fd);
          toast.success(`${file.name} added to your agent's documents.`);
          await refresh();
        } catch (e) {
          toast.error(
            errMessage(
              e,
              `Your front desk was created, but ${file.name} could not be uploaded. Add it on its page.`,
            ),
          );
        }
      }
      const schedules = created.schedules ?? [];
      if (schedules.length > 0) {
        // Show what was set up before leaving; closing the dialog finishes the same way as the button.
        setMade({ agent: created, schedules });
        setBusy(false);
        onBusy(false);
        onDone(() => onCreated(created, t, schedules));
      } else {
        onCreated(created, t, schedules);
      }
    } catch (e) {
      clearTimeout(timer);
      if (gaveUp) {
        // The server may still finish: refresh the list so a retry finds it ("You already have ... Open it instead?").
        setTimedOut(true);
        void Promise.resolve(refresh()).catch(() => undefined);
      } else {
        toast.error(
          errMessage(e, "Could not create the front desk from this template."),
        );
      }
      setBusy(false);
      onBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!orgClean || langsMissing || busy) return;
    const sameName = existing.find(
      (a) => a.name.trim().toLowerCase() === preview.name.trim().toLowerCase(),
    );
    if (sameName && !dupe) {
      setDupe(sameName);
      return;
    }
    void create();
  };

  const pickFile = (f: File | undefined) => {
    if (!f) return;
    if (!DOC_OK.test(f.name)) {
      toast.error(`${f.name}: only PDF, TXT and MD files are supported.`);
      return;
    }
    setFile(f);
  };

  if (made) {
    return (
      <div className="space-y-5" data-testid="desk-created">
        <div className="flex items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-success-tint text-accent-success">
            <Check className="size-5" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="font-medium text-text-primary">{made.agent.name} is ready</p>
            <p className="text-sm text-text-secondary">
              It also set up {made.schedules.length === 1 ? "this schedule" : "these schedules"} for bookings.
            </p>
          </div>
        </div>
        <ul aria-label="Schedules created" className="divide-y divide-border overflow-hidden rounded-xl border border-border">
          {made.schedules.map((s) => (
            <li key={s.id}>
              <Link
                href={`/desk/bookings/${encodeURIComponent(s.id)}`}
                className="flex min-h-14 items-center gap-3 px-4 py-3 hover:bg-bg-base focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent-primary"
              >
                <span
                  aria-hidden
                  className="grid size-9 shrink-0 place-items-center rounded-lg"
                  style={{ background: ACCENTS[type.accent].mid }}
                >
                  <CalendarCheck className="size-4 text-black" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-text-primary">{s.name}</span>
                  {scheduleLine(s) && (
                    <span className="block text-xs text-text-secondary">{scheduleLine(s)}</span>
                  )}
                </span>
              </Link>
            </li>
          ))}
        </ul>
        <p className="text-xs text-text-secondary">You can change hours, intervals and holidays any time in Bookings.</p>
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Link
            href="/desk/bookings"
            className="inline-flex h-10 items-center justify-center rounded-lg border border-border bg-bg-surface px-4 text-sm font-semibold text-text-primary hover:bg-bg-base focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-primary"
          >
            Open Bookings
          </Link>
          <Button onClick={() => onCreated(made.agent, t, made.schedules)} data-autofocus>
            Go to my front desk
          </Button>
        </div>
      </div>
    );
  }

  if (busy) {
    return <CreatingProgress accent={ACCENTS[type.accent].tint} label="Working for" />;
  }

  if (dupe) {
    return (
      <div className="space-y-4">
        <div
          role="alert"
          className="flex gap-3 rounded-lg border border-accent-warning/40 bg-accent-warning/10 p-3 text-sm text-text-primary"
        >
          <TriangleAlert
            className="mt-0.5 size-4 shrink-0 text-warning-text"
            aria-hidden
          />
          <p>You already have an agent called {dupe.name}. Open it instead?</p>
        </div>
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="ghost" onClick={() => setDupe(null)} disabled={busy}>
            Go back
          </Button>
          <Button
            variant="outline"
            onClick={() => void create()}
            loading={busy}
          >
            Create another one
          </Button>
          <Button
            onClick={() => onCreated(dupe, t)}
            disabled={busy}
            data-autofocus
          >
            Open {dupe.name.length > 28 ? "it" : dupe.name}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {timedOut && (
        <div
          role="alert"
          className="flex flex-col gap-3 rounded-lg border border-accent-warning/40 bg-accent-warning/10 p-3 text-sm text-text-primary sm:flex-row sm:items-center sm:justify-between"
          data-testid="desk-timeout"
        >
          <p className="flex gap-2">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning-text" aria-hidden />
            <span>
              This is taking longer than expected, so we stopped waiting. It may still finish in the background. Your details are kept below.
            </span>
          </p>
          <Button type="submit" size="sm" variant="outline" className="shrink-0">
            Try again
          </Button>
        </div>
      )}
      <div
        className="-mx-5 -mt-5 mb-1 flex items-center gap-4 px-5 py-4 sm:-mx-6 sm:px-6"
        style={{ background: ACCENTS[type.accent].tint }}
      >
        <DeskIllustration id={t.id} variant="scene" size={120} className="rounded-lg border border-black/10" />
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[1px] text-text-primary">{templateLabel(t)}</p>
          <p className="mt-1 text-sm text-text-secondary">
            Your front desk answers calls in the name you type below. Everything can be changed later.
          </p>
        </div>
      </div>

      <div>
        <label htmlFor="tpl-org" className={labelClass}>
          {orgLabel} <span className="text-accent-error">*</span>
        </label>
        <input
          id="tpl-org"
          className={fieldClass}
          value={org}
          onChange={(e) => setOrg(e.target.value)}
          onBlur={() => setTouched(true)}
          maxLength={80}
          placeholder={type.namePlaceholder}
          aria-required="true"
          aria-invalid={orgMissing}
          aria-describedby={orgMissing ? "tpl-org-err" : undefined}
          data-autofocus
        />
        {orgMissing && (
          <p id="tpl-org-err" className="mt-1.5 text-xs text-accent-error">
            Type the name callers will hear.
          </p>
        )}
      </div>

      <div>
        <label htmlFor="tpl-city" className={labelClass}>
          City or area{" "}
          <span className="font-normal text-text-secondary">(optional)</span>
        </label>
        <input
          id="tpl-city"
          className={fieldClass}
          value={city}
          onChange={(e) => setCity(e.target.value)}
          maxLength={60}
          placeholder="e.g. Nashik"
        />
      </div>

      <div>
        <label htmlFor="tpl-details" className={labelClass}>
          Opening hours, or what callers usually ask{" "}
          <span className="font-normal text-text-secondary">(optional)</span>
        </label>
        <textarea
          id="tpl-details"
          className={`${fieldClass} h-20 resize-y`}
          value={details}
          onChange={(e) => setDetails(e.target.value)}
          maxLength={1000}
          placeholder="e.g. Open Monday to Saturday, 9 to 6. People mostly ask about fees and booking."
        />
      </div>

      <fieldset>
        <legend className={labelClass}>Languages your agent speaks</legend>
        <div className="flex flex-wrap gap-2">
          {choices.map((code) => {
            const on = langs.includes(code);
            return (
              <label
                key={code}
                className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-accent-primary ${
                  on
                    ? "border-accent-primary bg-accent-primary/10 text-text-primary"
                    : "border-border text-text-secondary"
                }`}
              >
                <input
                  type="checkbox"
                  className="sr-only"
                  checked={on}
                  onChange={() => toggleLang(code)}
                />
                {languageName(code)}
              </label>
            );
          })}
        </div>
        {touched && langsMissing && (
          <p className="mt-1.5 text-xs text-accent-error">
            Pick at least one language.
          </p>
        )}
      </fieldset>

      <div>
        <span className={labelClass}>
          Your own document{" "}
          <span className="font-normal text-text-secondary">
            (optional, PDF, TXT or MD)
          </span>
        </span>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
          className="sr-only"
          aria-label="Choose your own document"
          onChange={(e) => {
            pickFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {file ? (
          <div className="flex min-h-11 items-center justify-between gap-2 rounded-lg border border-border bg-bg-elevated px-3 text-sm">
            <span className="truncate">{file.name}</span>
            <button
              type="button"
              onClick={() => setFile(null)}
              className="flex size-9 items-center justify-center rounded-md text-text-secondary hover:text-text-primary"
              aria-label={`Remove ${file.name}`}
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            leftIcon={<UploadCloud className="size-4" aria-hidden />}
            onClick={() => fileRef.current?.click()}
          >
            Upload a document now
          </Button>
        )}
        {!file && (
          <label className="mt-3 flex min-h-11 cursor-pointer items-start gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              className="mt-1 size-4"
              checked={withSample}
              onChange={(e) => setWithSample(e.target.checked)}
            />
            <span>
              Or add later. Meanwhile, include a sample document so I can try
              the agent now. Its details are made up and it is labelled
              &quot;Sample&quot;.
            </span>
          </label>
        )}
        {file && (
          <p className="mt-1.5 text-xs text-text-secondary">
            Your document is added right after the agent is created. No sample
            is added.
          </p>
        )}
      </div>

      <p
        className="rounded-lg bg-bg-elevated p-3 text-sm text-text-secondary"
        aria-live="polite"
      >
        <span className="font-medium text-text-primary">
          Your agent will greet callers as:{" "}
        </span>
        <span>&ldquo;{preview.greeting}&rdquo;</span>
      </p>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button
          type="button"
          variant="ghost"
          onClick={onCancel}
          disabled={busy}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          loading={busy}
          leftIcon={
            busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : undefined
          }
        >
          Create front desk
        </Button>
      </div>
    </form>
  );
}

/* ------------------------------------------------------------------ the cards */

const chipBase = "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium leading-none text-text-primary";

/** A sample line is written for any business, so a per-type stand-in ("[your clinic]", "[आपका होटल]") takes the owner's place. */
export const sampleText = (line: string, id: string, lang: "en" | "hi") => {
  const s = sampleStandIns(id, lang);
  return fillPlaceholders(line, s.org, s.city);
};

function SampleQuote({ line, id }: { line: { en: string; hi: string }; id: string }) {
  const [lang, setLang] = useState<"en" | "hi">("en");
  return (
    <figure className="rounded-xl bg-bg-base px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <figcaption className="text-[11px] font-semibold uppercase tracking-[1px] text-text-secondary">It might say</figcaption>
        <div role="group" aria-label="Sample line language" className="flex rounded-md border border-border bg-white p-0.5">
          {(
            [
              ["en", "EN"],
              ["hi", "HI"],
            ] as const
          ).map(([code, text]) => (
            <button
              key={code}
              type="button"
              aria-pressed={lang === code}
              aria-label={code === "en" ? "English" : "Hindi"}
              onClick={() => setLang(code)}
              className={`min-h-7 min-w-9 rounded px-2 text-xs font-semibold focus-visible:outline-2 focus-visible:outline-accent-primary [@media(pointer:coarse)]:min-h-9 ${
                lang === code ? "bg-black text-white" : "text-text-secondary hover:text-text-primary"
              }`}
            >
              {text}
            </button>
          ))}
        </div>
      </div>
      <blockquote lang={lang} className="mt-2 text-sm leading-relaxed text-text-primary">
        &ldquo;{sampleText(line[lang], id, lang)}&rdquo;
      </blockquote>
    </figure>
  );
}

function DeskCard({
  t,
  onUse,
}: {
  t: TemplateInfo;
  onUse: (t: TemplateInfo) => void;
}) {
  const type = deskType(t.id);
  const accent = ACCENTS[t.accent ?? type.accent] ?? ACCENTS.violet;
  const label = templateLabel(t);
  const tagline = t.tagline ?? t.audience ?? t.role;
  const persona = t.persona;
  return (
    <article
      className="flex h-full flex-col overflow-hidden rounded-2xl border border-border bg-white transition-colors hover:border-border-3"
      data-desk={t.id}
    >
      <div style={{ background: accent.tint }}>
        <DeskIllustration id={t.illustration ?? t.id} variant="scene" />
      </div>
      <div className="flex flex-1 flex-col gap-4 p-5">
        <div>
          <h3 className="font-display text-xl font-normal leading-tight text-text-primary">{label}</h3>
          <p className="mt-1 text-sm text-text-secondary">{tagline}</p>
        </div>
        {persona && (
          <ul aria-label="How it sounds" className="flex flex-wrap gap-1.5">
            <li className={chipBase} style={{ background: accent.mid, borderColor: accent.mid }}>
              {persona.tone}
            </li>
            {persona.traits.slice(0, 3).map((trait) => (
              <li key={trait} className={chipBase} style={{ borderColor: accent.mid, background: accent.tint }}>
                {trait}
              </li>
            ))}
          </ul>
        )}
        {persona?.sample_line && <SampleQuote line={persona.sample_line} id={t.id} />}
        <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1">
          <p className="text-xs text-text-secondary">
            <span className="sr-only">Languages: </span>
            {t.languages.map(languageName).join(", ")}
          </p>
          <div className="flex items-center gap-2">
            {t.sample_pdf ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void downloadSamplePdf(t)}
                aria-label={`Download sample PDF for ${label}`}
              >
                Sample PDF
              </Button>
            ) : null}
            <Button onClick={() => onUse(t)} aria-label={`Use this front desk: ${label}`}>
              Use this front desk
            </Button>
          </div>
        </div>
      </div>
    </article>
  );
}

function ScratchCard({ onStart }: { onStart: () => void }) {
  const type = deskType("custom");
  return (
    <article
      className="flex h-full flex-col overflow-hidden rounded-2xl border border-dashed border-border-3 bg-white"
      data-desk="custom"
    >
      <div style={{ background: ACCENTS.violet.tint }}>
        <DeskIllustration id="custom" variant="scene" />
      </div>
      <div className="flex flex-1 flex-col gap-4 p-5">
        <div>
          <h3 className="font-display text-xl font-normal leading-tight text-text-primary">Start from scratch</h3>
          <p className="mt-1 text-sm text-text-secondary">
            {type.tagline} Not a clinic, hotel or salon? Say what your front desk does and Vakya writes its instructions.
          </p>
        </div>
        <div className="mt-auto pt-1">
          <Button variant="outline" onClick={onStart} aria-label="Start from scratch: describe your front desk">
            Describe it myself
          </Button>
        </div>
      </div>
    </article>
  );
}

/** "Pick a front desk": one illustrated card per kind. Choosing one asks for the organisation's own details first. */
export function TemplateGallery({ onCreated, onStartFromScratch }: Props) {
  const templates = useAgentTemplates();
  const agents = useAgents();
  const [chosen, setChosen] = useState<TemplateInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const finishRef = useRef<(() => void) | null>(null);
  const ordered = useMemo(() => bookingFirst((templates.data ?? []) as TemplateInfo[]), [templates.data]);

  const close = () => {
    if (busy) return;
    // After creation the agent exists: closing the success step lands on it like the button does.
    const finish = finishRef.current;
    finishRef.current = null;
    setChosen(null);
    setDone(false);
    finish?.();
  };

  if (templates.error) return null; // templates are a convenience; the create form still works
  return (
    <section aria-labelledby="templates-heading" className="space-y-4">
      <div>
        <h2 id="templates-heading" className="font-display text-2xl font-normal text-text-primary">
          Pick your kind of front desk
        </h2>
        <p className="mt-1 max-w-[60ch] text-sm text-text-secondary">
          Each one has its own manner, sample answers and booking schedule. Choose one, then give it your organisation&apos;s name.
        </p>
      </div>
      {templates.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-96 w-full rounded-2xl" />
          ))}
        </div>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {ordered.map((t) => (
            <li key={t.id}>
              <DeskCard t={t} onUse={setChosen} />
            </li>
          ))}
          {onStartFromScratch && (
            <li>
              <ScratchCard onStart={onStartFromScratch} />
            </li>
          )}
        </ul>
      )}
      <Modal
        open={!!chosen}
        onClose={close}
        title={done ? "Your front desk is ready" : busy ? "Creating your front desk" : "Name your organisation"}
        description={
          chosen && !done && !busy
            ? `A ${templateLabel(chosen).toLowerCase()} front desk. Tell us who it will answer calls for.`
            : undefined
        }
        dismissible={!busy}
      >
        {chosen && (
          <TemplateForm
            key={chosen.id}
            template={chosen}
            existing={agents.data ?? []}
            onCancel={close}
            onCreated={(a, t, schedules) => {
              finishRef.current = null;
              setChosen(null);
              setBusy(false);
              setDone(false);
              onCreated(a, t, schedules);
            }}
            onBusy={setBusy}
            refresh={() => agents.mutate()}
            onDone={(finish) => {
              finishRef.current = finish;
              setDone(true);
            }}
          />
        )}
      </Modal>
    </section>
  );
}
