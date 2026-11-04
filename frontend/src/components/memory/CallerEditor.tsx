"use client";

import { useId, useState, type ReactNode } from "react";
import { Plus, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { errMessage } from "@/components/desk/format";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CallerEdit, CallerProfile } from "./types";

const input = "h-9 w-full min-w-0 rounded-lg border border-border bg-bg-base px-2.5 text-sm text-text-primary outline-none focus-visible:border-text-secondary";
const LANGUAGES: [string, string][] = [["", "Not known"], ["hi", "Hindi"], ["mr", "Marathi"], ["en", "English"], ["hinglish", "Hinglish"]];
const RELATIONS = ["", "son", "daughter", "wife", "husband", "mother", "father", "brother", "sister", "grandmother", "grandfather", "friend", "child"];
const BANDS = ["", "child", "teen", "adult", "senior"];

interface FactRow { key: string; label: string; value: string }
interface PersonRow { name: string; relation: string; age_band: string }

/** A detail's key from the words the owner typed: "Preferred doctor" -> "preferred_doctor". */
export function factKey(label: string): string {
  return label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 31);
}

function Field({ label, children }: { label: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="mb-1 block text-xs font-semibold uppercase tracking-wide text-text-secondary">{label}</label>
      {children(id)}
    </div>
  );
}

export interface CallerEditorProps {
  profile: CallerProfile;
  onSaved: (updated: CallerProfile) => void | Promise<unknown>;
  onCancel: () => void;
}

/**
 * The owner's inline edit of what is remembered about a caller: the name, the language, each detail and person, and the notes
 * for the next call (PUT /api/callers/{id}). Something Vakya never keeps (an identity number, a card, an OTP, a password, a
 * health detail) is refused by the server with its reason, shown here.
 */
export function CallerEditor({ profile, onSaved, onCancel }: CallerEditorProps) {
  const [name, setName] = useState(profile.display_name ?? "");
  const [language, setLanguage] = useState(profile.preferred_language ?? "");
  const [facts, setFacts] = useState<FactRow[]>((profile.facts ?? []).map((f) => ({ ...f })));
  const [people, setPeople] = useState<PersonRow[]>((profile.people ?? []).map((p) => ({ name: p.name, relation: p.relation ?? "", age_band: p.age_band ?? "" })));
  const [notes, setNotes] = useState<string[]>(profile.follow_ups.map((f) => f.note));
  const [newNote, setNewNote] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newValue, setNewValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addFact = () => {
    const key = factKey(newLabel);
    if (!key || !newValue.trim()) return;
    setFacts((rows) => [...rows.filter((r) => r.key !== key), { key, label: newLabel.trim(), value: newValue.trim() }]);
    setNewLabel("");
    setNewValue("");
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    const body: CallerEdit = { display_name: name.trim() || null, preferred_language: language || null };
    const facts_: Record<string, string | null> = {};
    for (const f of profile.facts ?? []) if (!facts.some((r) => r.key === f.key)) facts_[f.key] = null;
    for (const f of facts) facts_[f.key] = f.value.trim() || null;
    body.facts = facts_;
    body.people = people.filter((p) => p.name.trim()).map((p) => ({ name: p.name.trim(), relation: p.relation || null, age_band: p.age_band || null }));
    if (notes.length !== profile.follow_ups.length) body.follow_ups = notes;
    if (newNote.trim()) body.add_note = newNote.trim();
    try {
      const updated = await api.put<CallerProfile>(`/api/callers/${encodeURIComponent(profile.id)}`, body);
      toast.success("Saved. The next call uses this.");
      await onSaved(updated);
    } catch (e) {
      setError(errMessage(e, "Could not save. Try again."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      aria-label="Edit what is remembered"
      data-testid="caller-editor"
      className="mt-3 space-y-4 rounded-lg border border-border bg-bg-elevated p-3"
      onSubmit={(e) => { e.preventDefault(); void save(); }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">{(id) => <input id={id} className={input} value={name} maxLength={60} onChange={(e) => setName(e.target.value)} autoComplete="off" />}</Field>
        <Field label="Language">
          {(id) => (
            <select id={id} className={input} value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              {language && !LANGUAGES.some(([v]) => v === language) && <option value={language}>{language}</option>}
            </select>
          )}
        </Field>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-xs font-semibold uppercase tracking-wide text-text-secondary">People on this number</legend>
        {people.length === 0 && <p className="text-sm text-text-secondary">Nobody yet. Add the people this number books for.</p>}
        {people.map((p, i) => (
          <div key={i} className="grid grid-cols-[1fr_auto] items-end gap-2 sm:grid-cols-[1fr_8rem_7rem_auto]">
            <input aria-label={`Person ${i + 1} name`} className={cn(input, "col-span-2 sm:col-span-1")} value={p.name} maxLength={60} placeholder="Name"
              onChange={(e) => setPeople((rows) => rows.map((r, j) => (j === i ? { ...r, name: e.target.value } : r)))} />
            <select aria-label={`Person ${i + 1} relation`} className={input} value={p.relation}
              onChange={(e) => setPeople((rows) => rows.map((r, j) => (j === i ? { ...r, relation: e.target.value } : r)))}>
              {RELATIONS.map((r) => <option key={r} value={r}>{r || "Relation"}</option>)}
            </select>
            <select aria-label={`Person ${i + 1} age band`} className={input} value={p.age_band}
              onChange={(e) => setPeople((rows) => rows.map((r, j) => (j === i ? { ...r, age_band: e.target.value } : r)))}>
              {BANDS.map((r) => <option key={r} value={r}>{r || "Age band"}</option>)}
            </select>
            <button type="button" aria-label={`Remove person ${i + 1}`} onClick={() => setPeople((rows) => rows.filter((_, j) => j !== i))}
              className="grid size-9 place-items-center rounded-lg text-text-secondary hover:bg-bg-base hover:text-text-primary"><X className="size-4" /></button>
          </div>
        ))}
        {people.length < 8 && (
          <Button type="button" size="sm" variant="ghost" leftIcon={<Plus className="size-3.5" />} onClick={() => setPeople((rows) => [...rows, { name: "", relation: "", age_band: "" }])}>Add a person</Button>
        )}
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs font-semibold uppercase tracking-wide text-text-secondary">Details</legend>
        {facts.length === 0 && <p className="text-sm text-text-secondary">No details yet.</p>}
        {facts.map((f, i) => (
          <div key={f.key} className="grid grid-cols-[7.5rem_1fr_auto] items-center gap-2 sm:grid-cols-[10rem_1fr_auto]">
            <span className="min-w-0 break-words text-sm text-text-secondary">{f.label}</span>
            <input aria-label={f.label} className={input} value={f.value} maxLength={100}
              onChange={(e) => setFacts((rows) => rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))} />
            <button type="button" aria-label={`Remove ${f.label}`} onClick={() => setFacts((rows) => rows.filter((_, j) => j !== i))}
              className="grid size-9 place-items-center rounded-lg text-text-secondary hover:bg-bg-base hover:text-text-primary"><X className="size-4" /></button>
          </div>
        ))}
        <div className="grid gap-2 sm:grid-cols-[10rem_1fr_auto]">
          <input aria-label="New detail name" className={input} value={newLabel} maxLength={30} placeholder="e.g. Preferred doctor" onChange={(e) => setNewLabel(e.target.value)} />
          <input aria-label="New detail value" className={input} value={newValue} maxLength={100} placeholder="Value" onChange={(e) => setNewValue(e.target.value)} />
          <Button type="button" size="sm" variant="outline" onClick={addFact} disabled={!factKey(newLabel) || !newValue.trim()} disabledReason="Give the detail a name and a value">Add</Button>
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs font-semibold uppercase tracking-wide text-text-secondary">Notes for the next call</legend>
        {notes.map((n, i) => (
          <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-border bg-bg-base px-2.5 py-1.5 text-sm text-text-primary">
            <span className="min-w-0 break-words">{n}</span>
            <button type="button" aria-label={`Remove note ${i + 1}`} onClick={() => setNotes((rows) => rows.filter((_, j) => j !== i))} className="shrink-0 text-text-secondary hover:text-text-primary"><X className="size-4" /></button>
          </div>
        ))}
        <input aria-label="New note for the next call" className={input} value={newNote} maxLength={160} placeholder="Something to ask or mention next time"
          onChange={(e) => setNewNote(e.target.value)} />
      </fieldset>

      <p className="text-xs text-text-secondary">Vakya does not keep identity numbers, card numbers, OTPs, passwords or health details.</p>
      {error && <p role="alert" className="text-sm text-accent-error">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" loading={saving}>Save</Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </form>
  );
}
