/**
 * The public marketing pages, in reading order. One list feeds the nav, the footer, the "Go deeper" cards, the "Next"
 * links, the sitemap and the check that decides which routes get the marketing page treatment (see isSitePath).
 * `blurb` is one plain sentence that only restates what the page contains.
 */
export interface SitePage {
  href: string;
  /** Short label for the nav and footer. */
  nav: string;
  /** Heading of the card that links here. */
  title: string;
  blurb: string;
}

export const HOME: SitePage = {
  href: "/home",
  nav: "Home",
  title: "Vakya",
  blurb: "Call once, it's booked: a voice front desk for clinics, hotels and small businesses that reads every number back and leaves a signed receipt.",
};

/** Every page after Home, in the order a visitor would read them. */
export const SITE_PAGES: SitePage[] = [
  {
    href: "/how-it-works",
    nav: "How it works",
    title: "How it works",
    blurb: "The call path from an 8 kHz phone line through Gnani Prisma and Timbre to a booked slot and a signed receipt.",
  },
  {
    href: "/report-card",
    nav: "Report card",
    title: "Report card",
    blurb: "The same booking calls played clean, on an 8 kHz phone line and in street noise, with the failures shown.",
  },
  {
    href: "/theatre",
    nav: "Caller Theatre",
    title: "Caller Theatre",
    blurb: "Nine synthetic callers, one of them an edge case, run end to end and scored on what ended up in the booking system.",
  },
  {
    href: "/run-it",
    nav: "Run it",
    title: "Run it",
    blurb: "Five commands to run Vakya on your own machine, and the keys it needs.",
  },
  {
    href: "/faq",
    nav: "FAQ",
    title: "FAQ",
    blurb: "Who has used it, what it costs, which services it uses, and what is not ready yet.",
  },
];

/** Home first, then SITE_PAGES. */
export const ALL_SITE_PAGES: SitePage[] = [HOME, ...SITE_PAGES];

/** The page after this one in reading order, or undefined for the last. */
export function nextPage(href: string): SitePage | undefined {
  const i = ALL_SITE_PAGES.findIndex((p) => p.href === href);
  return i >= 0 ? ALL_SITE_PAGES[i + 1] : undefined;
}

/** True for the marketing pages (not for /desk, /verify or the auth routes). */
export function isSitePath(pathname: string | null | undefined): boolean {
  if (!pathname) return false;
  const p = pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  return ALL_SITE_PAGES.some((page) => page.href === p);
}

const LEGACY_HASHES: Record<string, string> = {
  consent: "/how-it-works#consent",
  pipeline: "/how-it-works#how",
  how: "/how-it-works#how",
  proof: "/report-card",
  numbers: "/report-card",
  rig: "/run-it",
  "rig-check": "/run-it",
  faq: "/faq",
};

function decodeSafe(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** Map an old single-page anchor to where that content lives now. Returns null when the hash still belongs to Home
 *  (#why, #products, #hear, #start ...) or is unknown, so nothing is redirected. Case-insensitive; the leading "#" is optional. */
export function mapLegacyHash(hash: string): string | null {
  const id = decodeSafe(hash.replace(/^#/, "")).toLowerCase();
  return Object.prototype.hasOwnProperty.call(LEGACY_HASHES, id) ? LEGACY_HASHES[id] : null;
}
