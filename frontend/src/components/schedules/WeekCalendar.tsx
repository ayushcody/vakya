"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { cn } from "@/lib/utils";
import { SlotHoverCard, useSlotHover } from "./SlotHoverCard";
import {
  DAY_LONG,
  DAY_SHORT,
  GAP_H,
  ROW_H,
  dayKeyOf,
  accentColor,
  buildGrid,
  dayLabelLong,
  dragSelection,
  isoDate,
  nowIn,
  offsetOf,
  rangeWords,
  isoHHMM,
  slotAt,
  slotLabel,
  slotSpan,
  timeWords,
  type CellState,
  type Schedule,
  type Slot,
  type SlotDay,
  type SlotsResponse,
} from "@/lib/schedules";

const HEADER_H = 54;
const TIME_W = 56;
const LONG_PRESS_MS = 320;

/** Diagonal hatching for "Busy": a pattern, so the state does not depend on colour alone. */
export const HATCH: CSSProperties = {
  backgroundImage:
    "repeating-linear-gradient(135deg, transparent 0 5px, color-mix(in srgb, var(--color-text-primary) 16%, transparent) 5px 6.5px)",
};

/** How a cell looks for a state. `fill` is the share of a partly booked slot that is taken. */
export function cellStyle(status: CellState, accent: string, fill = 0): { className: string; style?: CSSProperties } {
  const tint = (pct: number) => `color-mix(in srgb, ${accentColor(accent)} ${pct}%, var(--color-bg-surface))`;
  switch (status) {
    case "free":
      return { className: "bg-bg-surface text-text-secondary hover:border-[var(--cell-accent)] hover:bg-[var(--cell-tint)]" };
    case "booked":
      return {
        className: "border-[var(--cell-accent)] text-text-primary",
        style: { backgroundImage: `linear-gradient(to right, ${tint(34)} ${Math.round(fill * 100)}%, var(--color-bg-surface) ${Math.round(fill * 100)}%)` },
      };
    case "full":
      return { className: "border-transparent font-medium text-text-primary", style: { background: tint(38) } };
    case "blocked":
      return { className: "border-border-3 bg-bg-base text-text-primary", style: HATCH };
    case "past":
      return { className: "border-dashed bg-bg-base/60 text-text-secondary opacity-60" };
    case "closed":
      return { className: "bg-bg-base text-text-secondary" };
  }
}

const interactive = (s: CellState) => s === "free" || s === "booked" || s === "full" || s === "blocked";

function statusWords(slot: Slot, capacity: number): string {
  switch (slot.status) {
    case "free":
      return "free";
    case "booked":
      return `partly booked${slot.bookings.length ? ` by ${slot.bookings.map((b) => b.name ?? "a caller").join(", ")}` : ""}, ${slot.remaining} of ${capacity} left`;
    case "full":
      return slot.bookings[0]?.name ? `full, booked by ${slot.bookings.map((b) => b.name ?? "a caller").join(", ")}` : "full";
    case "blocked":
      return "busy";
    case "past":
      return "in the past";
  }
}

export interface WeekCalendarProps {
  schedule: Schedule;
  /** The dates to show: seven for a week, one for the phone's day view. */
  days: string[];
  data: SlotsResponse | undefined;
  loading: boolean;
  nowMs: number;
  /** Start times (ISO) of slots the answer panel is pointing at. */
  highlight: string[];
  compact: boolean;
  onOpenSlot: (slot: Slot, el: HTMLElement, following: Slot[]) => void;
  onBlockRange: (start: string, end: string) => void;
}

/**
 * The week as a compact calendar: sticky time column and day headers, one row per slot, the now line, and
 * press-and-drag down a column to mark a range busy. Arrow keys move between slots; Enter opens the slot.
 */
export function WeekCalendar({ schedule, days, data, loading, nowMs, highlight, compact, onOpenSlot, onBlockRange }: WeekCalendarProps) {
  const rowH = compact ? 40 : ROW_H;
  const gapH = GAP_H;
  const dayMap = useMemo(() => new Map((data?.days ?? []).map((d) => [d.date, d])), [data]);
  const shown = useMemo<(SlotDay | undefined)[]>(() => days.map((d) => dayMap.get(d)), [days, dayMap]);
  const grid = useMemo(() => buildGrid(shown.filter((d): d is SlotDay => !!d), schedule.slot_minutes), [shown, schedule.slot_minutes]);
  const rows = grid.rows;

  /** One column of cell states per visible day, top to bottom, for drag selection and arrow keys. */
  const columns = useMemo<CellState[][]>(
    () =>
      shown.map((day) =>
        rows.map((r) => {
          if (r.kind === "gap") return "closed";
          return slotAt(day, r.time)?.status ?? "closed";
        })
      ),
    [shown, rows]
  );
  const columnsRef = useRef(columns);
  useEffect(() => {
    columnsRef.current = columns;
  });

  const scroller = useRef<HTMLDivElement>(null);
  const hover = useSlotHover();
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [sel, setSel] = useState<{ col: number; start: number; end: number } | null>(null);
  const drag = useRef<{ col: number; from: number; to: number; active: boolean; timer: number | null; cleanup: () => void } | null>(null);
  const suppressClick = useRef(false);

  // The first interactive cell is the tab stop until the user moves.
  const firstKey = useMemo(() => {
    for (let r = 0; r < rows.length; r++) for (let c = 0; c < columns.length; c++) if (interactive(columns[c][r])) return `${c}:${r}`;
    return null;
  }, [rows, columns]);
  const tabStop = focusKey && (() => {
    const [c, r] = focusKey.split(":").map(Number);
    return columns[c]?.[r] && interactive(columns[c][r]) ? focusKey : null;
  })() || firstKey;

  // Open the week at the opening time, once per week (a refresh must not move what the user is looking at).
  const scrolledFor = useRef<string>("");
  const weekKey = `${schedule.id}:${days[0]}:${days.length}:${rows.length}`;
  useEffect(() => {
    const el = scroller.current;
    if (!el || !rows.length || scrolledFor.current === weekKey) return;
    scrolledFor.current = weekKey;
    const first = grid.first;
    const target = rows.length > 60 && first < 360 ? 480 : first;
    el.scrollTop = Math.max(0, (offsetOf(rows, target, grid.step, rowH, gapH) ?? 0) - 4);
  }, [weekKey, rows, grid.first, grid.step, rowH, gapH]);

  // Bring highlighted slots (the answer panel's options) into view.
  const hlKey = highlight.join("|");
  useEffect(() => {
    if (!hlKey) return;
    const first = hlKey.split("|")[0];
    const el = scroller.current?.querySelector<HTMLElement>(`[data-start="${first}"]`);
    el?.scrollIntoView?.({ block: "center", inline: "center", behavior: "smooth" });
  }, [hlKey, data]);

  const timeColumn = (minutes: number, first: boolean) => {
    const show = grid.step >= 30 || minutes % 60 === 0 || first;
    return show ? timeWords(`${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`) : "";
  };

  const slotFor = (col: number, row: number): Slot | undefined => {
    const r = rows[row];
    return r && r.kind === "slot" ? slotAt(shown[col], r.time) : undefined;
  };

  function endDrag(commit: boolean) {
    const d = drag.current;
    if (!d) return;
    d.cleanup();
    if (d.timer) window.clearTimeout(d.timer);
    drag.current = null;
    setSel(null);
    if (d.active) {
      suppressClick.current = true;
      window.setTimeout(() => (suppressClick.current = false), 0);
      const s = dragSelection(columnsRef.current[d.col] ?? [], d.from, d.to);
      const a = s && slotFor(d.col, s.start);
      const b = s && slotFor(d.col, s.end);
      if (commit && a && b) onBlockRange(a.start, b.end);
    }
  }

  function overCell(e: { clientX: number; clientY: number }): { col: number; row: number } | null {
    const el = document.elementFromPoint?.(e.clientX, e.clientY)?.closest?.<HTMLElement>("[data-col][data-row]");
    return el ? { col: Number(el.dataset.col), row: Number(el.dataset.row) } : null;
  }

  function onPointerDown(e: PointerEvent<HTMLButtonElement>, col: number, row: number) {
    if (e.button !== 0 || columnsRef.current[col]?.[row] !== "free") return;
    endDrag(false);
    const touch = e.pointerType === "touch";
    const move = (ev: globalThis.PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      const at = overCell(ev);
      if (!d.active) {
        if (touch) {
          // Moving before the press has been held means the finger is scrolling.
          if (d.timer) {
            window.clearTimeout(d.timer);
            d.timer = null;
          }
          return;
        }
        if (!at || at.col !== d.col || at.row === d.from) return;
        d.active = true;
      }
      if (at && at.col === d.col) {
        d.to = at.row;
        setSel({ col: d.col, ...(dragSelection(columnsRef.current[d.col], d.from, d.to) ?? { start: d.from, end: d.from }) });
      }
    };
    const up = () => endDrag(true);
    const cancel = () => endDrag(false);
    const key = (ev: globalThis.KeyboardEvent) => {
      if (ev.key === "Escape") endDrag(false);
    };
    const block = (ev: TouchEvent) => {
      if (drag.current?.active && ev.cancelable) ev.preventDefault();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", key);
    scroller.current?.addEventListener("touchmove", block, { passive: false });
    const sc = scroller.current;
    drag.current = {
      col,
      from: row,
      to: row,
      active: false,
      timer: null,
      cleanup: () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", cancel);
        window.removeEventListener("keydown", key);
        sc?.removeEventListener("touchmove", block);
      },
    };
    if (touch) {
      drag.current.timer = window.setTimeout(() => {
        const d = drag.current;
        if (!d) return;
        d.active = true;
        d.timer = null;
        setSel({ col: d.col, start: d.from, end: d.from });
      }, LONG_PRESS_MS);
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const t = (e.target as HTMLElement).closest<HTMLElement>("[data-col][data-row]");
    if (!t) return;
    let c = Number(t.dataset.col);
    let r = Number(t.dataset.row);
    const dc = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    const dr = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
    if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const cols = columnsRef.current;
      const list: number[] = [];
      cols[c]?.forEach((s, i) => interactive(s) && list.push(i));
      const target = e.key === "Home" ? list[0] : list[list.length - 1];
      if (target !== undefined) focusCell(c, target);
      return;
    }
    if (!dc && !dr) return;
    e.preventDefault();
    const cols = columnsRef.current;
    for (;;) {
      c += dc;
      r += dr;
      if (c < 0 || c >= cols.length || r < 0 || r >= rows.length) return;
      if (interactive(cols[c][r])) break;
    }
    focusCell(c, r);
  }
  function focusCell(c: number, r: number) {
    setFocusKey(`${c}:${r}`);
    scroller.current?.querySelector<HTMLElement>(`[data-col="${c}"][data-row="${r}"]`)?.focus();
  }

  const today = nowIn(schedule.timezone, nowMs);
  const todayCol = days.indexOf(today.date);
  const nowOffset = nowMs && todayCol >= 0 ? offsetOf(rows, today.minutes, grid.step, rowH, gapH) : null;
  const template = `${HEADER_H}px ${rows.map((r) => `${r.kind === "slot" ? rowH : gapH}px`).join(" ")}`;
  const cols = `${TIME_W}px repeat(${days.length}, minmax(${compact ? 0 : 92}px, 1fr))`;
  const hl = new Set(highlight);
  const accent = schedule.accent;
  const cssVars = { "--cell-accent": accentColor(accent), "--cell-tint": `color-mix(in srgb, ${accentColor(accent)} 12%, var(--color-bg-surface))` } as CSSProperties;

  if (!loading && data && rows.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border px-6 py-12 text-center">
        <p className="font-display text-lg text-text-primary">Nothing to book in this {days.length === 1 ? "day" : "week"}</p>
        <p className="mx-auto mt-1.5 max-w-sm text-sm text-text-secondary">
          The schedule is closed on these days, or they are holidays. Use the arrows to look at another week, or edit the schedule&apos;s days and hours.
        </p>
      </div>
    );
  }

  return (
    <div
      ref={scroller}
      role="grid"
      aria-label={`${schedule.name} calendar`}
      aria-rowcount={rows.length + 1}
      aria-busy={loading}
      onKeyDown={onKeyDown}
      style={cssVars}
      className="relative max-h-[min(72vh,46rem)] overflow-auto overscroll-contain rounded-2xl border border-border bg-bg-surface"
    >
      <SlotHoverCard target={hover.target} />
      {loading && !data ? (
        <div className="grid place-items-center py-24 text-sm text-text-secondary">Loading the calendar...</div>
      ) : (
        <div className="relative grid min-w-full" style={{ gridTemplateColumns: cols, gridTemplateRows: template }}>
          <div role="row" className="contents">
            <div role="columnheader" aria-label="Time" className="sticky left-0 top-0 z-30 border-b border-r border-border bg-bg-surface" />
            {days.map((d, c) => {
              const day = shown[c];
              const isToday = d === today.date;
              const closed = !!day && !day.open;
              const wk = (compact ? DAY_LONG : DAY_SHORT)[dayKeyOf(d)];
              const dateNum = Number(d.slice(8));
              return (
                <div
                  key={d}
                  role="columnheader"
                  aria-label={dayLabelLong(d) + (closed ? ", closed" : "")}
                  className={cn(
                    "sticky top-0 z-20 flex flex-col items-center justify-center border-b border-border bg-bg-surface px-1 text-center",
                    c > 0 && "border-l"
                  )}
                >
                  <span className="text-xs font-medium text-text-secondary">{wk}</span>
                  <span
                    className={cn(
                      "mt-0.5 grid h-7 min-w-7 place-items-center rounded-full px-1.5 text-[15px] font-semibold tabular-nums",
                      isToday ? "bg-bg-surface text-text-primary ring-2 ring-[var(--cell-accent)]" : "text-text-primary"
                    )}
                  >
                    {dateNum}
                  </span>
                  {closed && <span className="sr-only">closed</span>}
                </div>
              );
            })}
          </div>

          {rows.map((r, ri) => (
            <div key={`${r.kind}-${ri}`} role="row" aria-rowindex={ri + 2} className="contents">
              {r.kind === "slot" ? (
                <div
                  role="rowheader"
                  className="sticky left-0 z-10 -mt-px flex items-start justify-end border-r border-border bg-bg-surface pr-2 pt-0.5 text-[11px] leading-none text-text-secondary tabular-nums"
                >
                  <span className="-translate-y-0 whitespace-nowrap">{timeColumn(r.minutes, ri === 0)}</span>
                </div>
              ) : (
                <div role="rowheader" className="sticky left-0 z-10 border-r border-border bg-bg-base" />
              )}
              {days.map((d, c) => {
                if (r.kind === "gap") {
                  const mid = c === Math.floor(days.length / 2);
                  return (
                    <div key={d} role="gridcell" aria-label="Closed" className={cn("relative bg-bg-base", c > 0 && "border-l border-border")}>
                      {mid && (
                        <span className="absolute left-1/2 top-1/2 z-[1] -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-full bg-bg-base px-2 text-[11px] text-text-secondary">
                          Closed {rangeWords({ start: r.from, end: r.to })}
                        </span>
                      )}
                    </div>
                  );
                }
                const slot = slotAt(shown[c], r.time);
                const state: CellState = slot?.status ?? "closed";
                const cap = schedule.capacity;
                const fill = slot && state === "booked" ? 1 - slot.remaining / cap : 0;
                const look = cellStyle(state, accent, fill);
                const inSel = !!sel && sel.col === c && ri >= sel.start && ri <= sel.end;
                const highlighted = !!slot && hl.has(slot.start);
                // Rows of one busy block read as a single block: no seam between them, the word only once.
                const joined = state === "blocked" && !!slot?.block_id;
                const joinUp = joined && slotFor(c, ri - 1)?.block_id === slot!.block_id;
                const joinDown = joined && slotFor(c, ri + 1)?.block_id === slot!.block_id;
                return (
                  <div key={d} role="gridcell" aria-selected={inSel || undefined} className={cn("relative p-px", joinUp && "pt-0", joinDown && "pb-0", c > 0 && "border-l border-border")}>
                    {slot && interactive(state) ? (
                      <button
                        type="button"
                        data-col={c}
                        data-row={ri}
                        data-start={slot.start}
                        data-status={state}
                        tabIndex={tabStop === `${c}:${ri}` ? 0 : -1}
                        aria-label={`${dayLabelLong(d)}, ${slotSpan(slot.start, slot.end)}, ${statusWords(slot, cap)}`}
                        {...hover.bind(slot)}
                        onFocus={(e) => {
                          setFocusKey(`${c}:${ri}`);
                          hover.bind(slot).onFocus(e);
                        }}
                        onPointerDown={(e) => onPointerDown(e, c, ri)}
                        onContextMenu={(e) => e.preventDefault()}
                        onClick={(e) => {
                          if (suppressClick.current) return;
                          const following: Slot[] = [];
                          if (state === "free") {
                            for (let k = ri + 1; k < rows.length && columns[c][k] === "free"; k++) {
                              const nx = slotFor(c, k);
                              if (nx) following.push(nx);
                            }
                          }
                          onOpenSlot(slot, e.currentTarget, following);
                        }}
                        className={cn(
                          "group/cell flex size-full touch-pan-y select-none items-center justify-center overflow-hidden rounded-[5px] border border-border px-1 text-[11px] leading-none transition-colors",
                          "focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent-primary",
                          look.className,
                          joinUp && "rounded-t-none border-t-0",
                          joinDown && "rounded-b-none border-b-0",
                          inSel && "border-[var(--cell-accent)] bg-bg-base ring-1 ring-[var(--cell-accent)]",
                          highlighted && "z-[5] ring-2 ring-accent-primary ring-offset-1 ring-offset-bg-surface"
                        )}
                        style={inSel ? { ...HATCH } : look.style}
                      >
                        {inSel ? (
                          ri === sel!.start ? (
                            <span className="truncate font-medium text-text-primary">
                              Busy {rangeWords({ start: isoHHMM(slotFor(c, sel!.start)?.start ?? ""), end: isoHHMM(slotFor(c, sel!.end)?.end ?? "") })}
                            </span>
                          ) : null
                        ) : state === "free" ? (
                          <span aria-hidden className="text-[var(--cell-accent)] opacity-0 transition-opacity group-hover/cell:opacity-100">+</span>
                        ) : (
                          <span className="truncate">{joinUp ? "" : slotLabel(slot, cap)}</span>
                        )}
                      </button>
                    ) : (
                      <div
                        aria-label={slot ? `${dayLabelLong(d)}, ${slotSpan(slot.start, slot.end)}, ${statusWords(slot, cap)}` : "Closed"}
                        className={cn("size-full rounded-[5px]", look.className, slot?.status === "past" && "border")}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          ))}

          {nowOffset !== null && (
            <div
              aria-hidden
              className="pointer-events-none absolute z-[15] h-0"
              style={{ top: HEADER_H + nowOffset, left: TIME_W, right: 0 }}
            >
              <div className="h-0.5 w-full bg-accent-error opacity-70" />
              <div className="absolute -top-[4px] size-2.5 rounded-full bg-accent-error" style={{ left: `calc(${todayCol} * (100% / ${days.length}) - 5px)` }} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** The date a slot belongs to, for callers that only hold the slot. */
export const slotDate = (s: Slot) => isoDate(s.start);
