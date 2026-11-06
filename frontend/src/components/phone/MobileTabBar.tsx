"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentType } from "react";
import { BookingsIcon, CallsIcon, OverviewIcon, WeekIcon, type NavIconProps } from "@/components/icons/NavIcons";
import { cn } from "@/lib/utils";

const TABS: { label: string; href: string; icon: ComponentType<NavIconProps> }[] = [
  { label: "Today", href: "/desk", icon: OverviewIcon },
  { label: "Bookings", href: "/desk/bookings", icon: BookingsIcon },
  { label: "Calls", href: "/desk/calls", icon: CallsIcon },
  { label: "This week", href: "/desk/week", icon: WeekIcon },
];

/**
 * The desk on a phone: the four pages an owner opens between customers, one thumb away at the bottom of the screen.
 * Phones only (below md); every other page stays in the menu at the top. Clears the home indicator on an installed app.
 */
export function MobileTabBar() {
  const pathname = usePathname();
  const isActive = (href: string) => (href === "/desk" ? pathname === "/desk" : pathname === href || pathname.startsWith(href + "/"));
  return (
    <nav
      aria-label="Quick pages"
      data-area="desk"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
    >
      <ul className="mx-auto grid h-16 max-w-md grid-cols-4">
        {TABS.map(({ label, href, icon: Icon }) => {
          const active = isActive(href);
          return (
            <li key={href} className="flex">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative flex flex-1 flex-col items-center justify-center gap-1 text-[11px] font-medium transition-colors",
                  "focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-accent-primary",
                  active ? "text-text-primary" : "text-text-secondary hover:text-text-primary"
                )}
              >
                {active && <span aria-hidden className="absolute top-0 h-0.5 w-8 rounded-full bg-[var(--area-accent)]" />}
                <Icon active={active} className="size-[22px]" />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
