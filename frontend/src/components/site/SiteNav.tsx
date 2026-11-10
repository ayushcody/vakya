"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, Menu, X } from "lucide-react";
import { LayoutGroup } from "motion/react";
import { Magnetic, motion } from "./motion";
import { useLenis } from "./SmoothScroll";
import { Logo } from "@/components/Logo";
import { SITE_PAGES } from "./routes";

/** One link per page after Home (Home is the logo). The active page gets aria-current="page". */
const LINKS = SITE_PAGES.map((p) => [p.href, p.nav] as const);

const FOCUSABLE = "a[href], button:not([disabled]), [tabindex]:not([tabindex='-1'])";

/* A sticky bar on the off-white ground with a hairline under it. After the first scroll
   the hairline stays and nothing else changes: no pill, no bounce. */
const NAV_CSS = `
.site .nav-bar { background: var(--s-offwhite); border-bottom: 1px solid var(--s-line); }
`;

export function SiteNav() {
  const lenis = useLenis();
  const pathname = usePathname();
  const isCurrent = (href: string) => pathname === href || pathname === `${href}/`;
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  // A navigation (including Back) always closes the mobile sheet. Adjusted during render, not in an effect.
  const [seenPath, setSeenPath] = useState(pathname);
  if (seenPath !== pathname) {
    setSeenPath(pathname);
    setOpen(false);
  }
  const header = useRef<HTMLElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);

  const close = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) toggle.current?.focus();
  }, []);

  // Mobile sheet: lock page scroll, Escape closes, Tab stays inside the header, first link gets focus.
  useEffect(() => {
    if (!open) return;
    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = "hidden";
    lenis?.stop();
    // After a tick: the sheet is still visibility:hidden on the first frame of its transition, which blocks focus().
    const focusTimer = window.setTimeout(() => header.current?.querySelector<HTMLElement>("#site-menu a")?.focus(), 60);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close(true);
        return;
      }
      if (e.key !== "Tab" || !header.current) return;
      const items = Array.from(header.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.getClientRects().length > 0);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;
      if (e.shiftKey && current === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && current === last) {
        e.preventDefault();
        first.focus();
      } else if (!header.current.contains(current)) {
        e.preventDefault();
        first.focus();
      }
    };
    const onResize = () => {
      if (window.innerWidth >= 1024) close();
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onResize);
    return () => {
      window.clearTimeout(focusTimer);
      html.style.overflow = prev;
      lenis?.start();
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onResize);
    };
  }, [open, lenis, close]);

  return (
    <header
      ref={header}
      className="nav-bar fixed inset-x-0 top-0 z-50"
    >
      <style>{NAV_CSS}</style>
      <nav
        className="s-container flex h-16 items-center gap-4 xl:gap-8"
        aria-label="Main"
      >
        <Link href="/home" className="flex items-center" aria-label="Vakya home" aria-current={pathname === "/home" ? "page" : undefined}>
          <Logo size={30} />
        </Link>
        <LayoutGroup id="site-nav">
          <ul className="hidden items-center gap-1 lg:flex" onMouseLeave={() => setHovered(null)}>
            {LINKS.map(([href, label]) => {
              const current = isCurrent(href);
              const active = hovered === href || (hovered === null && current);
              return (
                <li key={href} className="relative">
                  {active && (
                    <motion.span
                      layoutId="nav-hover"
                      aria-hidden
                      className="absolute inset-0 rounded-lg bg-white"
                      transition={{ type: "spring", stiffness: 400, damping: 30 }}
                    />
                  )}
                  <Link
                    href={href}
                    aria-current={current ? "page" : undefined}
                    onMouseEnter={() => setHovered(href)}
                    onFocus={() => setHovered(href)}
                    onBlur={() => setHovered(null)}
                    className={`relative inline-flex min-h-10 items-center whitespace-nowrap rounded-lg px-3 text-sm font-medium transition-colors hover:text-[var(--s-ink)] ${
                      current ? "text-[var(--s-ink)]" : "text-[var(--s-ink-2)]"
                    }`}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </LayoutGroup>
        <div className="ml-auto flex items-center gap-2">
          {/* prefetch={false}: a prefetch would download the app's chunks (sign-in and Supabase client) to a visitor who never clicks. */}
          <span className="hidden xl:inline-flex">
          <Link
            href="/login"
            prefetch={false}
            className="s-btn s-btn--sm s-btn--secondary"
          >
            Sign in
          </Link>
          </span>
          <Magnetic strength={0.25}>
            <Link href="/desk" prefetch={false} className="group s-btn s-btn--sm s-btn--primary shrink-0">
              Open the desk
              <ArrowUpRight className="h-4 w-4 transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
            </Link>
          </Magnetic>
          <button
            ref={toggle}
            type="button"
            className="grid size-11 place-items-center rounded-lg text-[var(--s-ink)] transition-colors hover:bg-white lg:hidden"
            aria-expanded={open}
            aria-controls="site-menu"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X className="h-5 w-5" aria-hidden /> : <Menu className="h-5 w-5" aria-hidden />}
          </button>
        </div>
      </nav>

      {/* Mobile and tablet sheet. Absolute (not fixed) so the header's backdrop blur cannot become its containing block. */}
      <div
        id="site-menu"
        role="dialog"
        aria-modal="true"
        aria-label="Site menu"
        inert={!open}
        className={`absolute inset-x-0 top-full h-[calc(100dvh-4rem)] overflow-y-auto overscroll-contain border-t border-[var(--s-line)] bg-[var(--s-offwhite)] px-[var(--s-gutter)] pb-10 pt-4 transition-[opacity,visibility,transform] duration-300 motion-reduce:transition-none lg:hidden ${
          open ? "visible translate-y-0 opacity-100" : "invisible -translate-y-2 opacity-0"
        }`}
        data-lenis-prevent
      >
        <ul>
          {LINKS.map(([href, label]) => (
            <li key={href} className="border-b border-[var(--s-line)]">
              <Link
                href={href}
                onClick={() => close()}
                aria-current={isCurrent(href) ? "page" : undefined}
                className="flex min-h-14 items-center justify-between s-h3 py-2 text-[var(--s-ink)] aria-[current=page]:text-[var(--s-bronze)]"
              >
                {label}
                <ArrowUpRight className="h-5 w-5 text-[var(--s-mute)]" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Link href="/desk" prefetch={false} onClick={() => close()} className="s-btn s-btn--primary">
            Open the desk
          </Link>
          <Link href="/login" prefetch={false} onClick={() => close()} className="s-btn s-btn--secondary">
            Sign in
          </Link>
        </div>
      </div>
    </header>
  );
}
