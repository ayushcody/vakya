import { Globe, Phone, PhoneIncoming, UserCog } from "lucide-react";
import type { BookingSource } from "@/lib/schedules";

export const SOURCE_LABEL: Record<BookingSource, string> = {
  phone: "Phone call",
  missed_call: "Missed-call callback",
  web: "Browser call",
  staff: "Booked by staff",
};

const ICONS = { phone: Phone, missed_call: PhoneIncoming, web: Globe, staff: UserCog } as const;

/** A small icon for where a booking came from; its label is the text next to it or `title`. */
export function SourceIcon({ source, className = "size-4" }: { source?: BookingSource | null; className?: string }) {
  const Icon = ICONS[source ?? "phone"];
  return <Icon aria-hidden className={className} />;
}

export function ordinal(n: number): string {
  const v = n % 100;
  const suffix = ["th", "st", "nd", "rd"][(v - 20) % 10] ?? ["th", "st", "nd", "rd"][v] ?? "th";
  return `${n}${suffix}`;
}

const LANGUAGE: Record<string, string> = { en: "English", hi: "Hindi", mr: "Marathi", hinglish: "Hinglish", ta: "Tamil", te: "Telugu", kn: "Kannada", bn: "Bengali", gu: "Gujarati" };
export const languageName = (code?: string | null) => (code ? (LANGUAGE[code] ?? code) : null);
