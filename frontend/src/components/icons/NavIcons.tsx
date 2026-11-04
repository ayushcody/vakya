import React from "react";

/** The desk's navigation icons: 24px grid, 1.6 stroke, round caps, each built around the
 *  voice-bar motif from the logo. On the active item one detail lights up in the accent. */
export interface NavIconProps {
  className?: string;
  active?: boolean;
}

const MARIGOLD = "var(--color-accent-primary)";

function Frame({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      {children}
    </svg>
  );
}

/** The lit detail follows --area-accent (violet), or the primary accent when used outside the sidebar. */
const accent = (active?: boolean) => (active ? `var(--area-accent, ${MARIGOLD})` : "currentColor");

/** Today: three panels and a fourth that is speaking. */
export function OverviewIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <rect x="3" y="3" width="7.5" height="7.5" rx="2" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="2" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="2" />
      <g stroke={accent(active)}>
        <path d="M14.5 16v3" />
        <path d="M17.25 14v7" />
        <path d="M20 15.5v4" />
      </g>
    </Frame>
  );
}

/** Receptionist: a speech bubble that talks back. */
export function AgentsIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <path d="M4 6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-6l-4.5 4.2V16H7a3 3 0 0 1-3-3z" />
      <g stroke={accent(active)}>
        <path d="M9 8.5v2" />
        <path d="M12 7v5" />
        <path d="M15 8.5v2" />
      </g>
    </Frame>
  );
}

/** Try a call: signal arcs around a point. */
export function TryIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <path d="M7.8 7.8a6 6 0 0 0 0 8.4" />
      <path d="M16.2 7.8a6 6 0 0 1 0 8.4" />
      <path d="M4.9 4.9a10 10 0 0 0 0 14.2" />
      <path d="M19.1 4.9a10 10 0 0 1 0 14.2" />
      <circle cx="12" cy="12" r="2.3" fill={accent(active)} stroke={accent(active)} />
    </Frame>
  );
}

/** Report card: a trend line on a baseline, latest point marked. */
export function AnalyticsIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <path d="M3 20.5h18" />
      <path d="M4 16.5l4.2-5 3.6 3 4.4-6 2.2 2" />
      <circle cx="19.6" cy="11.2" r="1.7" fill={accent(active)} stroke={accent(active)} />
    </Frame>
  );
}

/** Check a receipt: a shield with a voice bar and a check beneath it. */
export function VerifyIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <path d="M12 3 4.5 5.8v5.6c0 4.4 3 7.6 7.5 9.6 4.5-2 7.5-5.2 7.5-9.6V5.8L12 3Z" />
      <g stroke={accent(active)}>
        <path d="m9 12.2 2.1 2.1L15.2 10" />
      </g>
    </Frame>
  );
}

/** Bookings: a calendar page with a tick. */
export function BookingsIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <rect x="3.5" y="5" width="17" height="15" rx="2.5" />
      <path d="M3.5 10h17" />
      <path d="M8 3v4" />
      <path d="M16 3v4" />
      <path d="m9 15 2.2 2.2L15.5 13" stroke={accent(active)} />
    </Frame>
  );
}

/** Calls: a phone handset with a voice bar. */
export function CallsIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <path d="M5.2 4h3.1l1.5 3.8-2 1.3a10.5 10.5 0 0 0 5.1 5.1l1.3-2 3.8 1.5v3.1a1.7 1.7 0 0 1-1.8 1.7A14.6 14.6 0 0 1 3.5 5.8 1.7 1.7 0 0 1 5.2 4Z" />
      <g stroke={accent(active)}>
        <path d="M15 4.5a5 5 0 0 1 4.5 4.5" />
        <path d="M15 8a1.6 1.6 0 0 1 1 1" />
      </g>
    </Frame>
  );
}

/** Callers: a person, remembered. */
export function CallersIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <circle cx="10" cy="8" r="3.4" />
      <path d="M3.5 20c.6-3.6 3.2-5.6 6.5-5.6s5.9 2 6.5 5.6" />
      <g stroke={accent(active)}>
        <path d="M19 6.5v4" />
        <path d="M17 8.5h4" />
      </g>
    </Frame>
  );
}

/** Settings: two sliders. */
export function SettingsIcon({ className, active }: NavIconProps) {
  return (
    <Frame className={className}>
      <path d="M4 8h16" />
      <path d="M4 16h16" />
      <circle cx="9" cy="8" r="2.2" fill="var(--color-bg-surface, #fff)" />
      <circle cx="15.5" cy="16" r="2.2" fill="var(--color-bg-surface, #fff)" stroke={accent(active)} />
    </Frame>
  );
}
