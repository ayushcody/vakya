"use client";

/**
 * The marketing site's Motion (motion.dev) vocabulary. Every showpiece interaction on the site is built from these few
 * pieces so the whole page moves with one character: a deceleration curve for entrances, light springs for anything
 * that follows the pointer, and nothing at all under reduced motion.
 *
 *   <SplitHeading text="One voice," accent="two ways to use it." />   words rise out of a clipped line, staggered
 *   <Reveal delay={0.1}>…</Reveal>                                     blur + lift on first view
 *   <Stagger>…<Item/>…</Stagger>                                       children reveal one after another
 *   <Magnetic>…</Magnetic>                                             the child leans toward the pointer
 *   <CountUp value="0.84 s" />                                         the number counts up on first view
 *
 * GSAP still drives the page's older [data-reveal] attributes and the Lenis scroll; these components never use those
 * attributes, so the two systems do not fight over an element.
 */

import {
  motion,
  useInView,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
  type Variants,
} from "motion/react";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

/** The site's deceleration curve (matches --s-ease in globals.css). */
export const EASE = [0.22, 1, 0.36, 1] as const;
/** A light spring for pointer-following motion. */
export const SOFT_SPRING = { stiffness: 150, damping: 20, mass: 0.4 };
/** The spring for cards and buttons that lift. */
export const LIFT_SPRING = { type: "spring", stiffness: 300, damping: 24 } as const;

/** True when the visitor prefers reduced motion (or before hydration, so the server and first client render agree). */
export function useStill(): boolean {
  const reduced = useReducedMotion();
  return reduced === true;
}

/* ------------------------------------------------------------------ split heading */

const lineVariants: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.055, delayChildren: 0.05 } },
};
const wordVariants: Variants = {
  hidden: { y: "110%", rotate: 3, opacity: 0.001 },
  show: { y: "0%", rotate: 0, opacity: 1, transition: { duration: 0.9, ease: EASE } },
};

type SplitHeadingProps = {
  /** The upright part of the heading. */
  text: string;
  /** A second phrase that follows it (same face and colour), on its own line when `break` is set. */
  accent?: string;
  /** Put the accent on a new line. */
  break?: boolean;
  /** Animate as soon as it mounts (the hero) instead of when it scrolls into view. */
  immediate?: boolean;
  delay?: number;
  as?: "h1" | "h2" | "h3" | "p";
  id?: string;
  className?: string;
};

/** A heading whose words rise one by one out of a clipped line. Server-rendered
 *  text is complete and in the DOM (screen readers and search engines see it whole); only the paint moves. */
export function SplitHeading({ text, accent, break: br, immediate, delay = 0, as = "h2", id, className = "" }: SplitHeadingProps) {
  const still = useStill();
  const Tag = motion[as];
  const words = (s: string, accentWord: boolean) =>
    s.split(" ").filter(Boolean).map((w, i, arr) => (
      <span key={`${accentWord ? "a" : "t"}${i}`} className="inline-block overflow-hidden pb-[0.08em] -mb-[0.08em] align-top">
        <motion.span variants={still ? undefined : wordVariants} className={`inline-block ${accentWord ? "accent" : ""}`}>
          {w}
          {i < arr.length - 1 ? " " : ""}
        </motion.span>
      </span>
    ));
  return (
    <Tag
      id={id}
      className={className}
      variants={still ? undefined : lineVariants}
      initial={still ? false : "hidden"}
      {...(immediate ? { animate: "show" } : { whileInView: "show", viewport: { once: true, amount: 0.4 } })}
      transition={{ delayChildren: delay }}
    >
      {words(text, false)}
      {accent && (
        <>
          {br ? <br /> : " "}
          {words(accent, true)}
        </>
      )}
    </Tag>
  );
}

/* ------------------------------------------------------------------ reveal */

type RevealProps = {
  children: ReactNode;
  delay?: number;
  /** Lift distance in px. */
  y?: number;
  /** Start slightly smaller as well (for images and panels). */
  scale?: boolean;
  /** Animate on mount instead of on view. */
  immediate?: boolean;
  as?: "div" | "p" | "li" | "section" | "span" | "figure";
  className?: string;
  style?: CSSProperties;
  id?: string;
  "aria-label"?: string;
  role?: string;
};

/** Fades, lifts and un-blurs its content the first time it scrolls into view. */
export function Reveal({ children, delay = 0, y = 20, scale, immediate, as = "div", className, style, ...rest }: RevealProps) {
  const still = useStill();
  const Tag = motion[as];
  if (still) {
    const Plain = as;
    return (
      <Plain className={className} style={style} {...rest}>
        {children}
      </Plain>
    );
  }
  return (
    <Tag
      className={className}
      style={style}
      initial={{ opacity: 0, y, scale: scale ? 0.96 : 1, filter: "blur(6px)" }}
      {...(immediate ? { animate: { opacity: 1, y: 0, scale: 1, filter: "blur(0px)" } } : { whileInView: { opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }, viewport: { once: true, margin: "0px 0px -12% 0px" } })}
      transition={{ duration: 0.9, ease: EASE, delay }}
      {...rest}
    >
      {children}
    </Tag>
  );
}

const staggerParent: Variants = { hidden: {}, show: { transition: { staggerChildren: 0.09 } } };
const staggerChild: Variants = {
  hidden: { opacity: 0, y: 24, filter: "blur(6px)" },
  show: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.9, ease: EASE } },
};

/** A list (or grid) whose children reveal one after another. Wrap each child in <Item>. */
export function Stagger({ children, as = "div", className, ...rest }: { children: ReactNode; as?: "div" | "ul" | "ol"; className?: string; role?: string; "aria-label"?: string; "aria-labelledby"?: string; id?: string }) {
  const still = useStill();
  const Tag = motion[as];
  return (
    <Tag className={className} variants={still ? undefined : staggerParent} initial={still ? false : "hidden"} whileInView="show" viewport={{ once: true, margin: "0px 0px -10% 0px" }} {...rest}>
      {children}
    </Tag>
  );
}

export function Item({ children, as = "div", className, style, ...rest }: { children: ReactNode; as?: "div" | "li" | "figure"; className?: string; style?: CSSProperties; lang?: string }) {
  const still = useStill();
  const Tag = motion[as];
  return (
    <Tag className={className} style={style} variants={still ? undefined : staggerChild} {...rest}>
      {children}
    </Tag>
  );
}

/* ------------------------------------------------------------------ magnetic */

/** The child leans toward the pointer while it hovers nearby and springs back when it leaves. Wrap a button or link. */
export function Magnetic({ children, strength = 0.35, className = "" }: { children: ReactNode; strength?: number; className?: string }) {
  const still = useStill();
  const ref = useRef<HTMLDivElement>(null);
  const x = useSpring(useMotionValue(0), SOFT_SPRING);
  const y = useSpring(useMotionValue(0), SOFT_SPRING);
  const onMove = (e: React.PointerEvent) => {
    if (still || e.pointerType !== "mouse") return;
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    x.set((e.clientX - (r.left + r.width / 2)) * strength);
    y.set((e.clientY - (r.top + r.height / 2)) * strength);
  };
  const reset = () => {
    x.set(0);
    y.set(0);
  };
  return (
    <motion.div ref={ref} className={`inline-flex ${className}`} style={{ x, y }} onPointerMove={onMove} onPointerLeave={reset}>
      {children}
    </motion.div>
  );
}

/* ------------------------------------------------------------------ count up */

/** The first number in `value` counts up from 0 the first time it is seen; the rest of the text stays put. The final
 *  text is in the server markup, so nothing is missing without JS. */
export function CountUp({ value, className, duration = 1.6 }: { value: string; className?: string; duration?: number }) {
  const still = useStill();
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -10% 0px" });
  const m = value.match(/\d[\d,]*(\.\d+)?/);
  const target = m ? parseFloat(m[0].replace(/,/g, "")) : 0;
  const decimals = m?.[1] ? m[1].length - 1 : 0;
  const before = m && m.index !== undefined ? value.slice(0, m.index) : "";
  const after = m && m.index !== undefined ? value.slice(m.index + m[0].length) : value;
  const [n, setN] = useState(target);

  useEffect(() => {
    if (still || !m || !inView) return;
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / (duration * 1000));
      const eased = 1 - Math.pow(1 - p, 4);
      setN(target * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    setN(0);
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, still]);

  const shown = m ? (m[0].includes(",") ? n.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : n.toFixed(decimals)) : "";
  return (
    <span ref={ref} className={`inline-block tabular-nums ${className ?? ""}`}>
      {before}
      {shown}
      {after}
    </span>
  );
}

export { motion, useScroll, useTransform, useSpring, useMotionValue, useMotionTemplate, useInView };
