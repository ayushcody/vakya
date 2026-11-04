"use client";

import type { ReactNode } from "react";
import { motion } from "motion/react";
import { useStill } from "@/components/site/motion";

const EASE_IN = { opacity: 1, y: 0, filter: "blur(0px)" };
// A different target, so switching to it after hydration re-runs the animation (instantly) for reduced-motion visitors.
const SHOW_NOW = { opacity: 1, y: 0, filter: "none" };

/** Next.js remounts a template on every navigation, so each marketing page eases in instead of snapping.
 *  The same element renders on the server and the client (see useStill), so hydration never leaves the page hidden. */
export default function Template({ children }: { children: ReactNode }) {
  const still = useStill();
  return (
    <motion.div
      initial={{ opacity: 0, y: 14, filter: "blur(6px)" }}
      animate={still ? SHOW_NOW : EASE_IN}
      transition={still ? { duration: 0 } : { duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}
