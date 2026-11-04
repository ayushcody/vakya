export type SortValue = string | number | null | undefined;

/** Ascending order of two cell values: numbers by size, text by locale (digits in order), empty values are the caller's job. */
export function compareValues(a: Exclude<SortValue, null | undefined>, b: Exclude<SortValue, null | undefined>): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

/** A stable sort by one value per row; rows with no value always go last, whichever way the list is sorted. */
export function sortRows<T>(rows: T[], value: (row: T) => SortValue, dir: "asc" | "desc"): T[] {
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i, v: value(row) }))
    .sort((x, y) => {
      const xe = x.v == null || x.v === "";
      const ye = y.v == null || y.v === "";
      if (xe || ye) return xe === ye ? x.i - y.i : xe ? 1 : -1;
      return sign * compareValues(x.v as string | number, y.v as string | number) || x.i - y.i;
    })
    .map((x) => x.row);
}
