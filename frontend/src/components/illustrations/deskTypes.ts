import type { DeskAccent } from "@/types/api";

/** The front-desk kinds the owner can pick. The ids are the template ids and the file names in /public/illustrations. */
export const DESK_IDS = [
  "clinic",
  "hotel",
  "salon",
  "restaurant",
  "office",
  "institute",
  "service_centre",
  "fitness",
] as const;
export type DeskId = (typeof DESK_IDS)[number];
export type DeskKey = DeskId | "custom";

export interface DeskTypeInfo {
  id: DeskKey;
  /** Short name for chips, selects and card titles. */
  label: string;
  tagline: string;
  accent: DeskAccent;
  /** Label of the "name your organisation" field. */
  nameLabel: string;
  /** Placeholder for it: a pointer to the owner's own name, never an invented business. */
  namePlaceholder: string;
  /** What stands for the business's own name in a sample line ("[your clinic]"), in English and Hindi. */
  standIn: { en: string; hi: string };
}

/** Fallback labels, used until (or unless) the server sends `type_label` and `tagline`. */
export const deskTypes: Record<DeskKey, DeskTypeInfo> = {
  clinic: { id: "clinic", label: "Clinic", tagline: "Timings, doctors and appointments.", accent: "blue", nameLabel: "Clinic name", namePlaceholder: "e.g. your clinic's name", standIn: { en: "[your clinic]", hi: "[आपका क्लिनिक]" } },
  hotel: { id: "hotel", label: "Hotel", tagline: "Rooms, check-in and enquiries.", accent: "amber", nameLabel: "Hotel name", namePlaceholder: "e.g. your hotel's name", standIn: { en: "[your hotel]", hi: "[आपका होटल]" } },
  salon: { id: "salon", label: "Salon and spa", tagline: "Services, prices and appointments.", accent: "rose", nameLabel: "Salon or spa name", namePlaceholder: "e.g. your salon's name", standIn: { en: "[your salon]", hi: "[आपका सैलून]" } },
  restaurant: { id: "restaurant", label: "Restaurant", tagline: "Menu, timings and table bookings.", accent: "orange", nameLabel: "Restaurant name", namePlaceholder: "e.g. your restaurant's name", standIn: { en: "[your restaurant]", hi: "[आपका रेस्टोरेंट]" } },
  office: { id: "office", label: "Professional office", tagline: "Consultations for a CA, lawyer or consultant.", accent: "slate", nameLabel: "Firm or office name", namePlaceholder: "e.g. your firm's name", standIn: { en: "[your office]", hi: "[आपका दफ़्तर]" } },
  institute: { id: "institute", label: "Coaching and admissions", tagline: "Courses, fees and campus visits.", accent: "violet", nameLabel: "Institute name", namePlaceholder: "e.g. your institute's name", standIn: { en: "[your institute]", hi: "[आपका संस्थान]" } },
  service_centre: { id: "service_centre", label: "Service centre", tagline: "Repairs, status and service slots.", accent: "teal", nameLabel: "Service centre name", namePlaceholder: "e.g. your service centre's name", standIn: { en: "[your service centre]", hi: "[आपका सर्विस सेंटर]" } },
  fitness: { id: "fitness", label: "Gym and studio", tagline: "Plans, classes and trial sessions.", accent: "green", nameLabel: "Gym or studio name", namePlaceholder: "e.g. your gym's name", standIn: { en: "[your gym]", hi: "[आपका जिम]" } },
  custom: { id: "custom", label: "Custom front desk", tagline: "Describe it in a sentence and it is yours.", accent: "violet", nameLabel: "Business name", namePlaceholder: "e.g. your business's name", standIn: { en: "[your business]", hi: "[आपका व्यवसाय]" } },
};

/** Soft fills for each accent, matching the colours painted into the illustrations (tint = wall, mid = counter, deep = details). */
export const ACCENTS: Record<DeskAccent, { tint: string; mid: string; deep: string }> = {
  blue: { tint: "#ecfbfe", mid: "#bef1fd", deep: "#6fcbe8" },
  amber: { tint: "#fff6dc", mid: "#fde3a0", deep: "#f2b84b" },
  rose: { tint: "#fef0f3", mid: "#fbc5d2", deep: "#f08ea6" },
  orange: { tint: "#fff3ea", mid: "#ffd3b7", deep: "#fe914b" },
  slate: { tint: "#eef0f4", mid: "#cfd5df", deep: "#7b879b" },
  violet: { tint: "#f1f0fd", mid: "#c4c0f8", deep: "#8981f1" },
  teal: { tint: "#e6f7f5", mid: "#a6e4dc", deep: "#38b2a3" },
  green: { tint: "#ecfdf3", mid: "#b6ebcf", deep: "#4fc08d" },
};

export const isDeskId = (id: unknown): id is DeskId => typeof id === "string" && (DESK_IDS as readonly string[]).includes(id);

/** The id to draw: a known front-desk id, or "custom" for anything else (old template ids, null, undefined). */
export const deskKey = (id: unknown): DeskKey => (isDeskId(id) ? id : "custom");

/** Label, tagline and accent for an id (falls back to the custom front desk). */
export const deskType = (id: unknown): DeskTypeInfo => deskTypes[deskKey(id)];

/** The stand-ins for a sample line that mentions the business or its city: "[your clinic]", "[आपका क्लिनिक]". */
export const sampleStandIns = (id: unknown, lang: "en" | "hi"): { org: string; city: string } => ({
  org: deskType(id).standIn[lang],
  city: lang === "hi" ? "[आपका शहर]" : "[your city]",
});

const STORE = "vakya.deskType.";

/** The server does not always store which type an agent came from, so the browser remembers it for agents made here. */
export function rememberDeskType(agentId: string, templateId: string | null | undefined): void {
  try {
    if (templateId && isDeskId(templateId)) window.localStorage.setItem(STORE + agentId, templateId);
  } catch {
    /* private window or blocked storage: the type just shows as custom */
  }
}

export function recalledDeskType(agentId: string): DeskId | null {
  try {
    const v = window.localStorage.getItem(STORE + agentId);
    return isDeskId(v) ? v : null;
  } catch {
    return null;
  }
}

/** The front-desk type of an agent: what the server stored, else what this browser remembers, else "custom". */
export function deskKeyOfAgent(agent: { id: string; template_id?: string | null } | null | undefined): DeskKey {
  if (!agent) return "custom";
  if (isDeskId(agent.template_id)) return agent.template_id;
  return recalledDeskType(agent.id) ?? "custom";
}
