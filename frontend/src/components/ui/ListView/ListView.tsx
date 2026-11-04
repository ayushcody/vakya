"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Check, Columns3, Rows2, Rows3 } from "lucide-react";
import { cn } from "@/lib/utils";
import { sortRows, type SortValue } from "./sort";

export type SortDir = "asc" | "desc";
export interface ListSort { id: string; dir: SortDir }
export type Density = "comfortable" | "compact";

/** Where a column goes on a phone (the list becomes two lines per row): line 1 has start, fill and end places, line 2 has fill and end. */
export interface StackedSlot { line: 1 | 2; slot: "start" | "fill" | "end" }

export interface CellContext {
  /** Rows sit under day (or other) group headings, so a cell can leave out what the heading already says. */
  grouped: boolean;
  /** The phone layout: two lines per row. */
  stacked: boolean;
}

export interface ListColumn<T> {
  id: string;
  /** The heading, and the name in the Columns menu. */
  header: string;
  cell: (row: T, ctx: CellContext) => ReactNode;
  /** Makes the column sortable by this value (empty values always sort last). */
  sortValue?: (row: T) => SortValue;
  /** A CSS grid track: "96px" or "minmax(120px, 1.4fr)". Default "minmax(0, 1fr)". May depend on whether rows sit under group headings. */
  width?: string | ((layout: { grouped: boolean }) => string);
  /** Pixels the column needs (a fixed track's own width); with `priority` it decides which columns drop out first when the list is narrow. Default 96. */
  minWidth?: number | ((layout: { grouped: boolean }) => number);
  /** Higher drops out sooner when there is no room. Default 0 (kept longest). */
  priority?: number;
  align?: "start" | "end";
  /** Cannot be switched off in the Columns menu or dropped for lack of room. */
  required?: boolean;
  /** Off until the person switches it on. */
  defaultHidden?: boolean;
  /** The phone layout; false (or leaving it out) keeps the column off the phone. */
  stacked?: StackedSlot | false;
}

export interface ListViewProps<T> {
  /** The accessible name of the list ("Calls"). */
  label: string;
  rows: T[];
  columns: ListColumn<T>[];
  getRowId: (row: T) => string;
  /** The highlighted row. */
  selectedId?: string | null;
  /** The highlight moved: a click, or an arrow key (up and down move it). */
  onSelect?: (row: T) => void;
  /** The row was opened: a click, Enter or Space. */
  onOpen?: (row: T) => void;
  /** The sort when nothing was chosen yet. */
  defaultSort?: ListSort;
  /** Controlled sort (with `onSortChange`). */
  sort?: ListSort;
  onSortChange?: (sort: ListSort) => void;
  /** Group rows under headings while the list is sorted by `groupSortId` (default: the default sort's column). */
  groupBy?: (row: T) => { key: string; label: string };
  groupSortId?: string;
  /** Rows that stay on top, under `pinnedLabel`, whatever the sort. */
  pinned?: (row: T) => boolean;
  pinnedLabel?: string;
  rowClassName?: (row: T) => string | undefined;
  /** Remembers the density and the hidden columns in this browser under this name. */
  storageKey?: string;
  defaultDensity?: Density;
  /** Search box, filters: left of the Columns and Density controls. */
  toolbar?: ReactNode;
  /** Shown instead of the rows when there are none. */
  empty?: ReactNode;
  /** Under the rows (a "showing 200 most recent" note). */
  footer?: ReactNode;
  /** CSS length the header sticks at while the page scrolls. Default 0. */
  stickyTop?: string;
  className?: string;
}

const STACK_BELOW = 640;   // px of list width
const GAP = 8;
const PAD = 16;
const ROW_H: Record<Density, string> = { comfortable: "min-h-11", compact: "min-h-[34px]" };

interface Prefs { density: Density; hidden: string[] | null }

function loadPrefs(key: string | undefined): Partial<Prefs> {
  if (!key) return {};
  try {
    const raw = JSON.parse(localStorage.getItem(`listview:${key}`) ?? "null");
    return raw && typeof raw === "object" ? raw : {};
  } catch {
    return {};
  }
}

function savePrefs(key: string | undefined, prefs: Prefs) {
  if (!key) return;
  try {
    localStorage.setItem(`listview:${key}`, JSON.stringify(prefs));
  } catch {
    /* a browser that keeps nothing is fine */
  }
}

type Item<T> = { kind: "group"; key: string; label: string } | { kind: "row"; row: T };

/**
 * A file-explorer list: a header with sortable columns, compact rows with hairlines, a highlighted row, up and down to
 * move, Enter to open, optional group headings and pinned rows, a Columns menu and a density switch. On a phone it
 * becomes two lines per row. It shows what it is given and sorts it; fetching, filtering and the preview pane belong to
 * the page.
 */
export function ListView<T>({
  label, rows, columns, getRowId, selectedId = null, onSelect, onOpen, defaultSort, sort: sortProp, onSortChange,
  groupBy, groupSortId, pinned, pinnedLabel = "Pinned", rowClassName, storageKey, defaultDensity = "comfortable",
  toolbar, empty, footer, stickyTop = "0px", className,
}: ListViewProps<T>) {
  const uid = useId();
  const root = useRef<HTMLDivElement>(null);
  const rowRefs = useRef(new Map<string, HTMLElement>());
  const [width, setWidth] = useState(0);
  const [density, setDensity] = useState<Density>(defaultDensity);
  const [hidden, setHidden] = useState<string[] | null>(null);   // null: the columns' own defaults
  const [ownSort, setOwnSort] = useState<ListSort | null>(defaultSort ?? null);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const p = loadPrefs(storageKey);
    if (p.density === "compact" || p.density === "comfortable") setDensity(p.density);
    if (Array.isArray(p.hidden)) setHidden(p.hidden.filter((x) => typeof x === "string"));
  }, [storageKey]);

  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 0));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!menu) return;
    const away = (e: Event) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    const esc = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc); };
  }, [menu]);

  const stacked = width > 0 && width < STACK_BELOW;
  const hiddenIds = useMemo(() => new Set(hidden ?? columns.filter((c) => c.defaultHidden).map((c) => c.id)), [hidden, columns]);

  const sort = sortProp ?? ownSort;
  const groupId = groupSortId ?? defaultSort?.id;
  const grouped = !!groupBy && !!sort && sort.id === groupId;
  const minOf = (c: ListColumn<T>) => (typeof c.minWidth === "function" ? c.minWidth({ grouped }) : c.minWidth ?? 96);
  const trackOf = (c: ListColumn<T>) => (typeof c.width === "function" ? c.width({ grouped }) : c.width ?? "minmax(0, 1fr)");

  // The columns shown: the ones switched on, then fewer while they do not fit (highest priority number goes first).
  const shown = useMemo(() => {
    let list = columns.filter((c) => c.required || !hiddenIds.has(c.id));
    if (!stacked && width > 0) {
      const need = (l: ListColumn<T>[]) => l.reduce((n, c) => n + minOf(c), 0) + GAP * Math.max(0, l.length - 1) + PAD * 2;
      while (need(list) > width) {
        const order = (c: ListColumn<T>) => (c.priority ?? 0) * 1000 + columns.indexOf(c);   // later columns of equal priority go first
        const drop = list.filter((c) => !c.required).sort((a, b) => order(b) - order(a))[0];
        if (!drop) break;
        list = list.filter((c) => c !== drop);
      }
    }
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns, hiddenIds, stacked, width, grouped]);

  const setSort = (next: ListSort) => { setOwnSort(next); onSortChange?.(next); };
  const sortCol = sort ? columns.find((c) => c.id === sort.id && c.sortValue) : undefined;

  const items = useMemo<Item<T>[]>(() => {
    const sorted = sortCol && sort ? sortRows(rows, sortCol.sortValue!, sort.dir) : rows;
    const top = pinned ? sorted.filter(pinned) : [];
    const rest = pinned ? sorted.filter((r) => !pinned(r)) : sorted;
    const out: Item<T>[] = [];
    if (top.length) {
      out.push({ kind: "group", key: "__pinned", label: pinnedLabel });
      top.forEach((row) => out.push({ kind: "row", row }));
    }
    let last: string | null = null;
    for (const row of rest) {
      if (grouped && groupBy) {
        const g = groupBy(row);
        if (g.key !== last) { out.push({ kind: "group", key: g.key, label: g.label }); last = g.key; }
      }
      out.push({ kind: "row", row });
    }
    return out;
  }, [rows, sortCol, sort, pinned, pinnedLabel, grouped, groupBy]);

  const ordered = useMemo(() => items.flatMap((i) => (i.kind === "row" ? [i.row] : [])), [items]);
  const active = ordered.find((r) => getRowId(r) === selectedId) ?? ordered[0];

  const move = useCallback((to: T | undefined) => {
    if (!to) return;
    onSelect?.(to);
    rowRefs.current.get(getRowId(to))?.focus();
  }, [onSelect, getRowId]);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const rowEl = (e.target as HTMLElement).closest<HTMLElement>("[data-row-id]");
    if (!rowEl) return;
    const i = ordered.findIndex((r) => getRowId(r) === rowEl.dataset.rowId);
    if (i < 0) return;
    const onRow = e.target === rowEl;
    if (e.key === "ArrowDown") { e.preventDefault(); move(ordered[Math.min(i + 1, ordered.length - 1)]); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(ordered[Math.max(i - 1, 0)]); }
    else if (e.key === "Home") { e.preventDefault(); move(ordered[0]); }
    else if (e.key === "End") { e.preventDefault(); move(ordered[ordered.length - 1]); }
    else if ((e.key === "Enter" || e.key === " ") && onRow) { e.preventDefault(); onSelect?.(ordered[i]); onOpen?.(ordered[i]); }
  };

  const click = (row: T, e: MouseEvent<HTMLElement>) => {
    if ((e.target as HTMLElement).closest("a,button,input,select,label")) return;   // a link or control inside the row does its own thing
    onSelect?.(row);
    onOpen?.(row);
  };

  const flip = (c: ListColumn<T>) => setSort({ id: c.id, dir: sort?.id === c.id && sort.dir === "asc" ? "desc" : "asc" });
  const toggle = (id: string) => {
    const now = new Set(hiddenIds);
    if (now.has(id)) now.delete(id); else now.add(id);
    const next = [...now];
    setHidden(next);
    savePrefs(storageKey, { density, hidden: next });
  };
  const chooseDensity = (d: Density) => { setDensity(d); savePrefs(storageKey, { density: d, hidden }); };

  const template = stacked ? "auto minmax(0, 1fr) auto" : shown.map(trackOf).join(" ");
  const gridStyle: CSSProperties = { gridTemplateColumns: template, columnGap: GAP, paddingInline: PAD };
  const sortable = columns.filter((c) => c.sortValue);

  const place = (s: StackedSlot): CSSProperties => ({
    gridRow: s.line,
    gridColumn: s.line === 1 ? (s.slot === "start" ? 1 : s.slot === "fill" ? 2 : 3) : s.slot === "fill" ? "1 / span 2" : 3,
  });

  return (
    <div className={cn("min-w-0", className)}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {toolbar}
        <div className="ml-auto flex items-center gap-2">
          {stacked && sortable.length > 0 && (
            <div className="flex items-center gap-1">
              <label htmlFor={`${uid}-sort`} className="sr-only">Sort by</label>
              <select
                id={`${uid}-sort`}
                value={sortCol?.id ?? ""}
                onChange={(e) => setSort({ id: e.target.value, dir: sort?.dir ?? "desc" })}
                className="h-9 rounded-lg border border-border bg-bg-surface px-2 text-sm text-text-primary"
              >
                {sortable.map((c) => <option key={c.id} value={c.id}>Sort: {c.header}</option>)}
              </select>
              <button
                type="button"
                onClick={() => sort && setSort({ ...sort, dir: sort.dir === "asc" ? "desc" : "asc" })}
                aria-label={sort?.dir === "asc" ? "Ascending, switch to descending" : "Descending, switch to ascending"}
                className="grid size-9 place-items-center rounded-lg border border-border bg-bg-surface text-text-secondary hover:text-text-primary"
              >
                {sort?.dir === "asc" ? <ArrowUp aria-hidden className="size-4" /> : <ArrowDown aria-hidden className="size-4" />}
              </button>
            </div>
          )}
          <div role="group" aria-label="Row density" className="flex rounded-lg border border-border bg-bg-surface p-0.5">
            {([["comfortable", Rows2, "Comfortable rows"], ["compact", Rows3, "Compact rows"]] as const).map(([d, Icon, name]) => (
              <button
                key={d}
                type="button"
                aria-pressed={density === d}
                aria-label={name}
                title={name}
                onClick={() => chooseDensity(d)}
                className={cn("grid size-8 place-items-center rounded-md text-text-secondary transition-colors hover:text-text-primary", density === d && "bg-bg-base text-text-primary")}
              >
                <Icon aria-hidden className="size-4" />
              </button>
            ))}
          </div>
          {!stacked && (
            <div ref={menuRef} className="relative">
              <button
                type="button"
                aria-expanded={menu}
                aria-controls={`${uid}-columns`}
                onClick={() => setMenu((m) => !m)}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-bg-surface px-3 text-sm font-medium text-text-primary hover:bg-bg-base"
              >
                <Columns3 aria-hidden className="size-4 text-text-secondary" />Columns
              </button>
              {menu && (
                <div id={`${uid}-columns`} role="group" aria-label="Columns" className="absolute right-0 top-full z-30 mt-1.5 w-56 rounded-xl border border-border bg-bg-surface p-1.5 shadow-[0_8px_24px_rgba(0,0,0,0.08)]">
                  {columns.map((c) => {
                    const on = c.required || !hiddenIds.has(c.id);
                    return (
                      <label key={c.id} className={cn("flex min-h-9 items-center gap-2.5 rounded-lg px-2.5 text-sm", c.required ? "text-text-secondary" : "cursor-pointer text-text-primary hover:bg-bg-base")}>
                        <input type="checkbox" checked={on} disabled={c.required} onChange={() => toggle(c.id)} className="peer sr-only" />
                        <span aria-hidden className={cn("grid size-4 place-items-center rounded border peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent-primary", on ? "border-accent-primary bg-accent-primary text-[var(--color-on-accent)]" : "border-border-3 bg-bg-surface")}>
                          {on && <Check className="size-3" strokeWidth={3} />}
                        </span>
                        {c.header}
                      </label>
                    );
                  })}
                  <p className="px-2.5 pb-1 pt-1.5 text-xs text-text-secondary">Columns that do not fit are left out for you.</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div ref={root} role="grid" aria-label={label} aria-rowcount={ordered.length + 1} onKeyDown={onKey} className="overflow-clip rounded-xl border border-border bg-bg-surface">
        {!stacked && (
          <div
            role="row"
            style={{ ...gridStyle, top: stickyTop }}
            className="sticky z-10 grid min-h-10 items-center border-b border-border bg-bg-elevated text-xs font-semibold text-text-secondary"
          >
            {shown.map((c) => {
              const dir = sort?.id === c.id ? sort.dir : null;
              const end = c.align === "end";
              return (
                <div key={c.id} role="columnheader" aria-sort={c.sortValue ? (dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none") : undefined} className={cn("min-w-0", end && "text-right")}>
                  {c.sortValue ? (
                    <button
                      type="button"
                      onClick={() => flip(c)}
                      className={cn("-mx-2 inline-flex h-8 max-w-full items-center gap-1 rounded-md px-2 transition-colors hover:bg-bg-base hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent-primary", end && "flex-row-reverse", dir && "text-text-primary")}
                    >
                      <span className="truncate">{c.header}</span>
                      {dir === "asc" ? <ArrowUp aria-hidden className="size-3.5 shrink-0" /> : dir === "desc" ? <ArrowDown aria-hidden className="size-3.5 shrink-0" /> : null}
                    </button>
                  ) : (
                    <span className="truncate">{c.header}</span>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {ordered.length === 0 && empty}

        {items.map((item) => {
          if (item.kind === "group") {
            return (
              <div key={`g-${item.key}`} role="row">
                <div role="rowheader" aria-colspan={shown.length} className={cn("flex items-center gap-2 border-b border-border-2 bg-bg-base px-4 text-xs font-semibold text-text-secondary", density === "compact" ? "min-h-7" : "min-h-8")}>
                  {item.key === "__pinned" && <span aria-hidden className="size-1.5 rounded-full bg-accent-success motion-safe:animate-pulse" />}
                  {item.label}
                </div>
              </div>
            );
          }
          const { row } = item;
          const id = getRowId(row);
          const selected = id === selectedId;
          return (
            <div
              key={id}
              ref={(el) => { if (el) rowRefs.current.set(id, el); else rowRefs.current.delete(id); }}
              role="row"
              data-row-id={id}
              aria-selected={onSelect ? selected : undefined}
              tabIndex={active && getRowId(active) === id ? 0 : -1}
              onClick={(e) => click(row, e)}
              style={stacked ? { gridTemplateColumns: template, columnGap: GAP, paddingInline: PAD, rowGap: 2 } : gridStyle}
              className={cn(
                "grid cursor-default items-center border-b border-border-2 outline-none transition-colors last:border-b-0",
                stacked ? "py-2.5" : ROW_H[density],
                !stacked && density === "compact" && "text-[13px]",
                selected ? "bg-violet-200/50 [[data-theme=dark]_&]:bg-accent-primary/15" : "hover:bg-bg-base",
                "focus-visible:bg-violet-200/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent-primary",
                rowClassName?.(row)
              )}
            >
              {stacked
                ? columns.filter((c) => c.stacked).map((c) => (
                    <div key={c.id} role="gridcell" style={place(c.stacked as StackedSlot)} className={cn("min-w-0", (c.stacked as StackedSlot).slot === "end" && "justify-self-end text-right")}>
                      {c.cell(row, { grouped, stacked })}
                    </div>
                  ))
                : shown.map((c) => (
                    <div key={c.id} role="gridcell" className={cn("min-w-0", c.align === "end" && "text-right")}>
                      {c.cell(row, { grouped, stacked })}
                    </div>
                  ))}
            </div>
          );
        })}
      </div>
      {footer}
    </div>
  );
}
