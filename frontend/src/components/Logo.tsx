import styles from "./Logo.module.css";

/**
 * The Vakya mark is a spoken sentence that ends in a full stop: five rounded bars staggered so their tops and bottoms
 * both trace a V (the sound), then the violet Devanagari danda (the commit). The wordmark is a monoline drawing with
 * the same 8-unit stroke as the bars. The static files in /public/brand are generated from the same numbers.
 */

/** `ink` is near-black for light surfaces, `light` is off-white for dark ones, `auto` follows the app theme. */
export type LogoTone = "ink" | "light" | "auto";
export type LogoVariant = "mark" | "lockup" | "wordmark";

const INK = "#0b0b0f";
const PAPER = "#fcf7f2";

/** Bar and stroke width in grid units. The mark is 64 units tall. */
const STROKE = 8;
const BAR_X = [0, 13, 26, 39, 52];
/** [top, bottom] of each of the five V bars. */
const BARS: ReadonlyArray<readonly [number, number]> = [
  [0, 38],
  [14, 50],
  [28, 64],
  [14, 50],
  [0, 38],
];
const DANDA_X = 71;
const MARK_W = 79;
const MARK_H = 64;

const WORD_PATHS = ["M0 28L14 60L28 28", "M74 28V60", "M92 4V60M113 28L92 45L114 60", "M129 28L143 60M157 28L132.5 84", "M205 28V60"];
const WORD_CIRCLES = [58, 189];

/** Lockup: mark, a 22 unit gap, then the wordmark whose first letter starts 4 units (half a stroke) inside its origin. */
const WORD_ORIGIN = MARK_W + 22 + 4;
const LOCKUP_W = WORD_ORIGIN + 209;
const LOCKUP_H = 88;
/** The wordmark on its own ends with a danda of its own: "vakya।". */
const WORDMARK_VIEWBOX = "-4 0 241 88";
const WORDMARK_ASPECT = 241 / 88;

const ASPECT: Record<LogoVariant, number> = {
  mark: MARK_W / MARK_H,
  lockup: LOCKUP_W / LOCKUP_H,
  wordmark: WORDMARK_ASPECT,
};
const VIEWBOX: Record<LogoVariant, string> = {
  mark: `0 0 ${MARK_W} ${MARK_H}`,
  lockup: `0 0 ${LOCKUP_W} ${LOCKUP_H}`,
  wordmark: WORDMARK_VIEWBOX,
};

function Bars() {
  return (
    <>
      {BARS.map(([top, bottom], i) => (
        <rect key={i} x={BAR_X[i]} y={top} width={STROKE} height={bottom - top} rx={STROKE / 2} className={styles.ink} />
      ))}
      <rect x={DANDA_X} y={0} width={STROKE} height={MARK_H} rx={STROKE / 2} className={styles.danda} />
    </>
  );
}

function Letters() {
  return (
    <g fill="none" strokeWidth={STROKE} strokeLinecap="round" strokeLinejoin="round" className={styles.stroke}>
      {WORD_PATHS.map((d) => (
        <path key={d} d={d} />
      ))}
      {WORD_CIRCLES.map((cx) => (
        <circle key={cx} cx={cx} cy={44} r={16} />
      ))}
    </g>
  );
}

interface LogoProps {
  /** Which drawing: the mark alone, the mark with the wordmark, or the wordmark alone (it ends in its own danda). */
  variant?: LogoVariant;
  /** Height in px. The width follows from the drawing's proportions. */
  size?: number;
  tone?: LogoTone | "dark";
  /** Accessible name. An empty string marks the drawing decorative (for example, beside visible text). */
  label?: string;
  className?: string;
  /** Legacy: `false` draws the mark alone. */
  showText?: boolean;
  /** Legacy: `true` draws the wordmark alone. */
  onlyText?: boolean;
  /** Legacy and ignored: the old VOICE AGENTS line is gone. */
  subtitle?: boolean;
}

/** Vakya logo as inline SVG. The ink follows `tone`; the danda stays violet through `--accent`. */
export function Logo({
  variant,
  size = 32,
  tone = "auto",
  label = "Vakya",
  className = "",
  showText = true,
  onlyText = false,
}: LogoProps) {
  const kind: LogoVariant = variant ?? (onlyText ? "wordmark" : showText ? "lockup" : "mark");
  const resolved = tone === "dark" ? "light" : tone;
  const decorative = label === "";
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={VIEWBOX[kind]}
      width={Math.round(size * ASPECT[kind] * 100) / 100}
      height={size}
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : label}
      aria-hidden={decorative ? true : undefined}
      focusable="false"
      data-variant={kind}
      className={`${styles.logo} ${styles[resolved]} ${className}`}
    >
      {kind !== "wordmark" && <Bars />}
      {kind === "lockup" && (
        <g transform={`translate(${WORD_ORIGIN} 0)`}>
          <Letters />
        </g>
      )}
      {kind === "wordmark" && (
        <>
          <Letters />
          <path d="M233 4V60" fill="none" strokeWidth={STROKE} strokeLinecap="round" className={styles.dandaStroke} />
        </>
      )}
    </svg>
  );
}

interface LogoMarkProps {
  /** Rendered height in px. */
  size?: number;
  /** Accessible name. Leave empty when the mark sits next to the wordmark or inside an already-labelled link. */
  label?: string;
  tone?: LogoTone | "dark";
  className?: string;
}

/** The mark alone. Decorative unless given a label. */
export function LogoMark({ size = 28, label = "", tone = "auto", className = "" }: LogoMarkProps) {
  return <Logo variant="mark" size={size} label={label} tone={tone} className={`shrink-0 ${className}`} />;
}

interface WordmarkProps {
  /** Height in px. */
  size?: number;
  tone?: LogoTone | "dark";
  label?: string;
  className?: string;
  /** Legacy and ignored. */
  subtitle?: boolean;
}

/** The monoline "vakya।" wordmark alone. */
export function Wordmark({ size = 22, tone = "auto", label = "Vakya", className = "" }: WordmarkProps) {
  return <Logo variant="wordmark" size={size} label={label} tone={tone} className={className} />;
}

export const LOGO_COLORS = { ink: INK, paper: PAPER, accent: "#6c62ed" } as const;
