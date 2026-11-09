"use client";

import { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

/** The pages the tour walks through, how long it stays on each and how far it scrolls (0..1 of the page). */
const STOPS: { path: string; ms: number; scroll: number }[] = [
  { path: "/desk", ms: 6000, scroll: 0.6 },
  { path: "/desk/bookings", ms: 7000, scroll: 0.7 },
  { path: "/desk/schedules", ms: 4000, scroll: 0 },
  { path: "/desk/bookings/S1", ms: 9000, scroll: 0.8 },    // the clinic's week: Wednesday evening full
];
const END = "/desk/calls?watch=1";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The element that scrolls the page: the tallest scrollable box (the desk scrolls its main area, not the window). */
function scroller(): HTMLElement {
  const boxes = Array.from(document.querySelectorAll<HTMLElement>("main, main *, body > div, body > div *"))
    .filter((el) => el.scrollHeight > el.clientHeight + 40 && /(auto|scroll)/.test(getComputedStyle(el).overflowY));
  boxes.sort((a, b) => b.clientHeight - a.clientHeight);
  return boxes[0] ?? (document.scrollingElement as HTMLElement) ?? document.documentElement;
}

async function glide(el: HTMLElement, to: number, ms: number) {
  const from = el.scrollTop;
  const start = performance.now();
  await new Promise<void>((done) => {
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      const ease = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      el.scrollTop = from + (to - from) * ease;
      if (t < 1) requestAnimationFrame(step); else done();
    };
    requestAnimationFrame(step);
  });
}

/** `?tour=1` on any desk page: a hands-free walk through Today, Bookings and Schedules (each scrolled slowly down and back
 *  up), ending on Calls with the live transcript ready to open, for a screen recording of the product. Off otherwise. */
export function DemoTour() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const on = params?.get("tour") === "1";

  useEffect(() => {
    if (!on) return;
    let stopped = false;
    (async () => {
      for (const stop of STOPS) {
        if (stopped) return;
        if (pathname !== stop.path) router.push(`${stop.path}?tour=1`);
        await wait(1500);                                   // the page loads its data
        const el = scroller();
        const down = (el.scrollHeight - el.clientHeight) * stop.scroll;
        if (down > 0) await glide(el, down, (stop.ms - 1500) * 0.6); else await wait(stop.ms - 1500);
        await wait(400);
        await glide(el, 0, (stop.ms - 1500) * 0.3);
      }
      if (stopped) return;
      try { sessionStorage.setItem("vk-watch", "1"); } catch { /* the URL flag still arms it */ }
      router.push(END);
    })();
    return () => { stopped = true; };
    // run once per tour start
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [on]);

  return null;
}
