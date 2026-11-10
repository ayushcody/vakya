/**
 * The demo film's chapters: where each part of the 3-minute video starts (seconds), read frame by frame from the video
 * and named after its own on-screen titles and labels ("Pick a front desk", "Checks real availability", ...). Each still
 * is the frame at that second (public/preview/moment-NN.jpg). Nothing here describes anything the film does not show.
 */
export interface Moment {
  id: string;
  at: number;
  title: string;
  line: string;
  still: string;
}

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
  src: "/preview/vakya-demo.mp4",
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
  kicker: string;
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
    kicker: "Animated · the real call",
    line: "A busy clinic and a returning patient, drawn. The dialogue is the recorded call, with the clinic's real screen beside it.",
  },
  {
    id: "ad",
    src: "/preview/vakya-ad.mp4",
    poster: "/preview/vakya-ad-poster.jpg",
    seconds: 59,
    title: "Vakya in a minute",
    label: "Vakya in a minute, short film",
    kicker: "Short film",
    line: "The real call, the WhatsApp messages, the booking's proof and the setup, in under a minute.",
  },
];

const still = (n: number) => `/preview/moment-${String(n).padStart(2, "0")}.jpg`;

export const MOMENTS: Moment[] = [
  { id: "call", at: 0, title: "A real phone call", line: "6 is taken, so the desk offers 5 or 7.", still: still(1) },
  { id: "today", at: 18, title: "Your day at a glance", line: "Today's appointments, as the owner sees them.", still: still(2) },
  { id: "desk", at: 27, title: "Pick a front desk", line: "Clinic, hotel, salon: each comes ready-made.", still: still(3) },
  { id: "schedule", at: 45, title: "Your schedule", line: "The hours the agent books into.", still: still(4) },
  { id: "line", at: 57, title: "The phone line", line: "Hearing: Gnani Prisma. Voice: Gnani Timbre.", still: still(5) },
  { id: "memory", at: 66, title: "Remembers returning callers", line: "\"Wednesday, 6 PM please.\"", still: still(6) },
  { id: "availability", at: 72, title: "Checks real availability", line: "6 is taken. 5 or 7?", still: still(7) },
  { id: "readback", at: 84, title: "Reads it back before booking", line: "Wednesday 7 PM, for Ayush, number ending 6295.", still: still(8) },
  { id: "whatsapp", at: 96, title: "Confirmation on WhatsApp", line: "Token 11, sent as the booking is made.", still: still(9) },
  { id: "move", at: 99, title: "Make it Thursday, 11 AM", line: "The new slot is read back before it changes.", still: still(10) },
  { id: "cancel", at: 126, title: "Cancel that booking", line: "Read back first: \"A107, Wednesday 7 PM. Shall I cancel it?\"", still: still(11) },
  { id: "proof", at: 159, title: "Proof after the call", line: "What was said, what was read back, and the yes.", still: still(12) },
  { id: "phone", at: 165, title: "On the caller's phone", line: "Every WhatsApp message, as the caller receives it.", still: still(13) },
  { id: "change", at: 171, title: "Change anything, anytime", line: "Hours and settings, edited in place.", still: still(14) },
];

/** "2:05" for 125 seconds. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
