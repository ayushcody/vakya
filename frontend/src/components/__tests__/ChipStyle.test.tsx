import { readFileSync } from "fs";
import { join } from "path";
import { fireEvent, render, screen } from "@testing-library/react";
import { DayChips } from "@/components/schedules/form/DayChips";
import { Chips } from "@/components/schedules/form/Fields";
import { CHIP_FOCUS } from "@/components/schedules/form/chipStyle";

const css = readFileSync(join(process.cwd(), "src/app/globals.css"), "utf8");
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const lum = ([r, g, b]: number[]) => {
  const f = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
const ratio = (a: number[], b: number[]) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const token = (name: string, from = 0) => {
  const m = new RegExp(`${name}:\\s*([^;]+);`).exec(css.slice(from));
  if (!m) throw new Error(`token ${name} missing`);
  return m[1].trim();
};

describe("selected chip style", () => {
  it("light theme: the chosen-chip text is at least 4.5:1 on its tint", () => {
    const bg = token("--chip-on-bg"); // var(--color-violet-200)
    const bgHex = bg.startsWith("var(") ? token(bg.slice(4, -1)) : bg;
    expect(ratio(rgb(bgHex), rgb(token("--chip-on-text")))).toBeGreaterThanOrEqual(4.5);
  });

  it("dark theme: the tint over the dark surface keeps 4.5:1", () => {
    const dark = css.indexOf('[data-theme="dark"]');
    const m = /--chip-on-bg:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/.exec(css.slice(dark))!;
    const a = Number(m[4]);
    const surface = rgb(token("--color-bg-surface", dark));
    const mixed = [1, 2, 3].map((i, k) => Math.round(Number(m[i]) * a + surface[k] * (1 - a)));
    expect(ratio(mixed, rgb(token("--chip-on-text", dark)))).toBeGreaterThanOrEqual(4.5);
  });

  it("slot-length chips: the chosen one uses the chip tokens, not black, and every chip has a focus ring", () => {
    render(<Chips label="Slot length" value={20} onChange={() => {}} options={[{ value: 15, label: "15 min" }, { value: 20, label: "20 min" }]} />);
    const on = screen.getByRole("radio", { name: "20 min" });
    expect(on.className).toContain("var(--chip-on-bg)");
    expect(on.className).toContain("var(--chip-on-text)");
    expect(on.className).not.toContain("bg-brand");
    const off = screen.getByRole("radio", { name: "15 min" });
    expect(off.className).not.toContain("--chip-on-bg");
    for (const el of [on, off]) expect(el.className).toContain(CHIP_FOCUS.split(" ")[0]);
  });

  it("day chips and day presets share the same chosen style", () => {
    const onChange = jest.fn();
    render(<DayChips open={["mon", "tue", "wed", "thu", "fri"]} onChange={onChange} />);
    const mon = screen.getByRole("button", { name: "Monday" });
    const sat = screen.getByRole("button", { name: "Saturday" });
    expect(mon.className).toContain("var(--chip-on-bg)");
    expect(sat.className).not.toContain("var(--chip-on-bg)");
    const weekdays = screen.getByRole("button", { name: "Weekdays" });
    expect(weekdays).toHaveAttribute("aria-pressed", "true");
    expect(weekdays.className).toContain("var(--chip-on-bg)");
    for (const el of [mon, sat, weekdays]) expect(el.className).toContain("focus-visible:outline-accent-primary");
    fireEvent.click(sat);
    expect(onChange).toHaveBeenCalled();
  });
});
