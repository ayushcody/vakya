import { deskTimezone } from "@/lib/formatDate";

/** "6:24:07 pm" in the desk's time zone, or "" for a time that is not one. */
export function clockWithSeconds(iso: string | null | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return "";
  return new Intl.DateTimeFormat("en-IN", { timeZone: deskTimezone(), hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true }).format(new Date(t)).replace(/\s?([ap])m/i, (_, x: string) => ` ${x.toLowerCase()}m`);
}

/** "1.4 s" for 1400 ms, "820 ms" under a second. */
export function ms(n: number): string {
  return n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(1)} s`;
}
