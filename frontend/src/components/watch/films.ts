/** A film the player can open: the demo, or one of the shorter films under it. */
export interface Film {
  id: string;
  src: string;
  poster: string;
  seconds: number;
  title: string;
  /** The player's accessible name. */
  label: string;
}

export const FILM = {
  id: "demo",
  src: "/landing/demo.mp4",   // the same film as the landing demo slot (one file, 15 MB)
  teaser: "/preview/teaser.mp4",
  poster: "/preview/poster.jpg",
  teaserPoster: "/preview/teaser-poster.jpg",
  seconds: 184,
  title: "Call once, it's booked.",
  label: "Vakya demo film",
};

/**
 * Two more films, built from the same real call and screen recording as the demo. "A real case" is animated: the
 * receptionist, Ayush and the clinic are drawn; their dialogue is the recorded call, and the clinic's real screen runs
 * beside it. The short film is the minute-long version. Lengths are the files' own (132.5 s and 59.0 s).
 */
export interface MoreFilm extends Film {
  line: string;
}

export const MORE_FILMS: MoreFilm[] = [
  {
    id: "real-case",
    src: "/preview/real-case.mp4",
    poster: "/preview/real-case-poster.jpg",
    seconds: 132,
    title: "A real case",
    label: "A real case, animated film",
    line: "A busy clinic and a returning patient, animated over the real call.",
  },
  {
    id: "ad",
    src: "/preview/vakya-ad.mp4",
    poster: "/preview/vakya-ad-poster.jpg",
    seconds: 59,
    title: "Vakya in a minute",
    label: "Vakya in a minute, short film",
    line: "The call, the WhatsApp messages and the proof, in under a minute.",
  },
];

/** "2:05" for 125 seconds. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
