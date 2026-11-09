"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ComponentType } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Menu, X, PanelLeftClose, PanelLeftOpen, LogOut } from "lucide-react";
import type { NavIconProps } from "./icons/NavIcons";
import {
  BookingsDuoIcon,
  CallsDuoIcon,
  FrontDeskIcon,
  ProofIcon,
  ScheduleIcon,
  SettingsDuoIcon,
  TodayIcon,
} from "./icons/DuoNavIcons";
import { useHealth } from "@/hooks/useApi";
import { useAuth } from "./AuthProvider";
import { Logo, LogoMark } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";

interface NavItem {
  label: string;
  href: string;
  icon: ComponentType<NavIconProps>;
  /** Pages reached from this item that should mark it as current (This week opens from Today, Callers from Calls). */
  also?: string[];
  /** Overrides the default "this page or below it" check. */
  match?: (pathname: string) => boolean;
}

/** The desk: the four pages a front desk uses every day, then the evidence. Each has its own icon. Try a call is the
 *  button on Today, This week opens from Today, Callers from Calls. The command palette lists every page. */
export const NAV: NavItem[] = [
  { label: "Today", href: "/desk", icon: TodayIcon, also: ["/desk/week"] },
  { label: "Bookings", href: "/desk/bookings", icon: BookingsDuoIcon, match: (p) => p === "/desk/bookings" },
  // A schedule opens at /desk/bookings/<id> (and a new one at /desk/bookings/new), so those pages belong to Schedules.
  { label: "Schedules", href: "/desk/schedules", icon: ScheduleIcon, match: (p) => p === "/desk/schedules" || p.startsWith("/desk/bookings/") },
  { label: "Calls", href: "/desk/calls", icon: CallsDuoIcon, also: ["/desk/callers"] },
  { label: "Front desk", href: "/desk/receptionist", icon: FrontDeskIcon, also: ["/desk/try"] },
  { label: "Proof", href: "/desk/proof", icon: ProofIcon, also: ["/desk/report-card", "/desk/theatre"] },
];

/** Settings sits apart at the foot of the list. */
const FOOT_ITEMS: NavItem[] = [{ label: "Settings", href: "/desk/settings", icon: SettingsDuoIcon }];

/** The one line at the foot of the sidebar. */
export type SystemState = "ready" | "starting" | "attention";
export const SYSTEM_LABEL: Record<SystemState, string> = { ready: "Ready", starting: "Starting", attention: "Needs attention" };
const SYSTEM_DOT: Record<SystemState, string> = {
  ready: "bg-accent-success",
  starting: "bg-accent-warning animate-pulse motion-reduce:animate-none",
  attention: "bg-accent-error",
};

/** apiDown: the server did not answer. `known` is false until the first answer. */
export function systemState(apiDown: boolean, known: boolean): SystemState {
  if (apiDown) return "attention";
  return known ? "ready" : "starting";
}

interface Props {
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}

export function DeskSidebar({ collapsed = false, onToggleCollapsed }: Props) {
  const pathname = usePathname();
  const { user, signOut } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);

  const health = useHealth();
  const apiDown = !!health.error && !health.data;
  // No Supabase on the server: there is no account to sign in to, everything lives on this computer.
  const localMode = health.data?.supabase?.configured === false;
  const system = systemState(apiDown, !!health.data);
  const systemHint = apiDown
    ? "Vakya cannot reach its server. Check that it is running."
    : system === "starting"
      ? "Connecting to the server."
      : "The server is running.";

  // Close the mobile drawer on navigation.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setDrawerOpen(false), [pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const under = (href: string) => pathname === href || pathname.startsWith(href + "/");
  const isActive = (href: string) => {
    const item = [...NAV, ...FOOT_ITEMS].find((i) => i.href === href);
    if (item?.match) return item.match(pathname);
    if (item?.also?.some(under)) return true;
    if (href === "/desk") return pathname === "/desk";
    return under(href);
  };

  const name =
    (user?.user_metadata?.name as string | undefined) ||
    (user?.user_metadata?.full_name as string | undefined) ||
    user?.email?.split("@")[0] ||
    (localMode ? "Local mode" : "Guest");

  const navLink = ({ label, href, icon: Icon }: NavItem, compact: boolean, inDrawer: boolean) => {
    const active = isActive(href);
    return (
      <Link
        key={href}
        href={href}
        title={compact ? label : undefined}
        aria-current={active ? "page" : undefined}
        className={cn(
          "group relative flex h-11 items-center rounded-lg md:h-9 text-sm font-medium transition-colors duration-150",
          compact ? "justify-center" : "gap-3 px-3",
          active ? "text-text-primary" : "text-text-secondary hover:text-text-primary"
        )}
      >
        {active && (
          <motion.span
            // Settings sits below a divider: its highlight appears in place instead of sliding across the line from the list
            layoutId={(inDrawer ? "nav-active-drawer" : "nav-active") + (FOOT_ITEMS.some((f) => f.href === href) ? "-foot" : "")}
            className="absolute inset-0 rounded-lg bg-[color-mix(in_srgb,var(--area-accent)_14%,transparent)]"
            transition={{ type: "spring", stiffness: 420, damping: 34 }}
          >
            <span aria-hidden className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full bg-[var(--area-accent)]" />
          </motion.span>
        )}
        {!active && <span aria-hidden className="absolute inset-0 rounded-lg bg-bg-base opacity-0 transition-opacity group-hover:opacity-100" />}
        <Icon active={active} className="relative size-[19px] shrink-0" />
        {!compact && <span className="relative min-w-0 flex-1 truncate">{label}</span>}
      </Link>
    );
  };

  /** `compact` hides labels (desktop collapsed). Drawer always renders expanded. */
  const content = (compact: boolean, inDrawer: boolean) => (
    <div data-area="desk" className={cn("flex h-full flex-col", inDrawer && "overflow-y-auto")}>
      <div className={cn("flex h-14 shrink-0 items-center border-b border-border", compact ? "justify-center" : "justify-between px-4")}>
        <Link href="/" aria-label="Vakya home" className="flex items-center">
          {compact ? <LogoMark size={28} /> : <Logo size={26} />}
        </Link>
        {inDrawer ? (
          <button onClick={() => setDrawerOpen(false)} aria-label="Close menu" className="grid size-11 place-items-center rounded-lg text-text-secondary hover:bg-bg-base hover:text-text-primary">
            <X className="size-5" />
          </button>
        ) : null}
      </div>

      {/* In the drawer the whole panel scrolls (one scroll area), so the list and the footer are always reachable on a short phone. */}
      <nav aria-label="Main" className={cn("flex-1 px-2 pb-3 pt-4", !inDrawer && "overflow-y-auto overflow-x-hidden")}>
        <div className="space-y-0.5">{NAV.map((item) => navLink(item, compact, inDrawer))}</div>
        <div role="group" aria-label="Settings" className={cn("mt-4 border-t border-border pt-3", compact && "mt-3")}>
          {FOOT_ITEMS.map((item) => navLink(item, compact, inDrawer))}
        </div>
      </nav>

      <div className="shrink-0 space-y-2.5 border-t border-border p-3">
        <div
          role="status"
          aria-label={`System status: ${SYSTEM_LABEL[system]}`}
          title={systemHint}
          className={cn("flex items-center text-xs text-text-secondary", compact ? "justify-center" : "gap-2 px-1")}
        >
          <span aria-hidden className={cn("size-2 shrink-0 rounded-full", SYSTEM_DOT[system])} />
          {!compact ? <span>{SYSTEM_LABEL[system]}</span> : <span className="sr-only">{SYSTEM_LABEL[system]}</span>}
        </div>

        <div className={cn("flex items-center", compact ? "flex-col gap-2" : "gap-2.5")}>
          <div className="grid size-8 shrink-0 place-items-center rounded-full bg-brand text-xs font-semibold text-[var(--color-on-accent)]" aria-hidden>
            {name.charAt(0).toUpperCase()}
          </div>
          {!compact && (
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-medium text-text-primary">{name}</p>
              <p className="truncate text-[11px] text-text-secondary">{user?.email ?? (localMode ? "Data stays on this computer" : "Not signed in")}</p>
            </div>
          )}
          <ThemeToggle />
          {user ? (
            <button onClick={() => signOut()} aria-label="Sign out" title="Sign out" className="grid size-9 place-items-center rounded-lg text-text-secondary transition-colors hover:bg-bg-base hover:text-accent-error">
              <LogOut className="size-4" />
            </button>
          ) : localMode ? null : (
            <Link href="/login" className="text-xs font-medium text-accent-secondary">Sign in</Link>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop */}
      <aside
        className={cn(
          "fixed left-0 top-0 z-40 hidden h-screen flex-col border-r border-border bg-bg-surface md:flex",
          "transition-[width] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
          collapsed ? "w-16" : "w-60"
        )}
      >
        {content(collapsed, false)}
        <button
          onClick={onToggleCollapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          className="absolute -right-3 top-[68px] grid size-6 place-items-center rounded-full border border-border bg-bg-surface text-text-secondary transition-colors hover:text-text-primary"
        >
          {collapsed ? <PanelLeftOpen className="size-3.5" /> : <PanelLeftClose className="size-3.5" />}
        </button>
      </aside>

      {/* Mobile top bar */}
      <div className="fixed inset-x-0 top-0 z-40 flex h-14 items-center gap-3 border-b border-border bg-bg-surface/95 px-3 backdrop-blur md:hidden">
        <button onClick={() => setDrawerOpen(true)} aria-label="Open menu" aria-expanded={drawerOpen} className="grid size-10 place-items-center rounded-lg text-text-primary hover:bg-bg-base">
          <Menu className="size-5" />
        </button>
        <Logo size={24} />
      </div>

      {/* Mobile drawer */}
      <AnimatePresence>
        {drawerOpen && (
          <>
            <motion.div
              key="scrim"
              className="fixed inset-0 z-50 bg-black/60 md:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setDrawerOpen(false)}
            />
            <motion.aside
              key="drawer"
              role="dialog"
              aria-modal="true"
              aria-label="Navigation"
              className="fixed inset-y-0 left-0 z-50 w-72 max-w-[85vw] border-r border-border bg-bg-surface md:hidden"
              initial={{ x: "-100%" }}
              animate={{ x: 0 }}
              exit={{ x: "-100%" }}
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            >
              {content(false, true)}
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </>
  );
}

export default DeskSidebar;
