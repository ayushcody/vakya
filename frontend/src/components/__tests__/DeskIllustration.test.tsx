import { fireEvent, render, screen } from "@testing-library/react";
import { DeskIllustration } from "@/components/illustrations/DeskIllustration";
import { DeskPicker } from "@/components/illustrations/DeskPicker";
import { ACCENTS, DESK_IDS, deskKey, deskKeyOfAgent, deskType, deskTypes, recalledDeskType, rememberDeskType } from "@/components/illustrations/deskTypes";
import { templateIdOf } from "@/lib/schedules";
import { ForEveryDesk } from "@/components/site/ForEveryDesk";
import fs from "node:fs";
import path from "node:path";

jest.mock("@/components/site/motion", () => {
  const pass = ({ children, as: Tag = "div", className }: { children: React.ReactNode; as?: React.ElementType; className?: string }) => <Tag className={className}>{children}</Tag>;
  return {
    Reveal: pass,
    Stagger: pass,
    Item: pass,
    SplitHeading: ({ text, accent, id }: { text: string; accent: string; id: string }) => (
      <h2 id={id}>
        {text} {accent}
      </h2>
    ),
  };
});

beforeEach(() => window.localStorage.clear());

describe("DeskIllustration", () => {
  it("draws the scene for a known front desk, decorative by default", () => {
    const { container } = render(<DeskIllustration id="hotel" />);
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/illustrations/hotel.svg");
    expect(img.getAttribute("alt")).toBe("");
    expect(img.getAttribute("aria-hidden")).toBe("true");
    expect(img.getAttribute("width")).toBe("320");
  });

  it("draws the 32 px glyph as an icon", () => {
    const { container } = render(<DeskIllustration id="salon" variant="icon" />);
    const img = container.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/illustrations/salon-icon.svg");
    expect(img.style.width).toBe("32px");
    expect(img.style.height).toBe("32px");
  });

  it("keeps the 320 x 220 ratio when given a width", () => {
    const { container } = render(<DeskIllustration id="clinic" size={160} />);
    const img = container.querySelector("img")!;
    expect(img.style.width).toBe("160px");
    expect(img.style.height).toBe("110px");
  });

  it("is announced only when it has a label", () => {
    render(<DeskIllustration id="fitness" label="A gym front desk" />);
    expect(screen.getByRole("img", { name: "A gym front desk" })).toBeTruthy();
  });

  it.each([[undefined], [null], [""], ["clinic_receptionist"], ["nonsense"]])("falls back to custom for %p", (id) => {
    const { container } = render(<DeskIllustration id={id as string | null | undefined} variant="icon" />);
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/illustrations/custom-icon.svg");
  });

  it("falls back to custom when the file fails to load", () => {
    const { container } = render(<DeskIllustration id="office" />);
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/illustrations/custom.svg");
  });
});

describe("the illustration files", () => {
  const dir = path.join(process.cwd(), "public", "illustrations");
  const ids = [...DESK_IDS, "custom"];
  it.each(ids)("%s has a scene under 8 KB and a 32 px glyph", (id) => {
    const scene = fs.readFileSync(path.join(dir, `${id}.svg`), "utf8");
    const icon = fs.readFileSync(path.join(dir, `${id}-icon.svg`), "utf8");
    expect(scene.length).toBeLessThan(8 * 1024);
    expect(scene).toContain('viewBox="0 0 320 220"');
    expect(icon).toContain('viewBox="0 0 32 32"');
    expect(scene).not.toMatch(/<metadata|sodipodi|inkscape|<text/);
    expect(scene).toContain("#6c62ed"); // the violet danda of the sound-bar motif
  });
});

describe("deskTypes", () => {
  it("has the eight front desks, each with a label, tagline and an accent that has colours", () => {
    expect(DESK_IDS).toHaveLength(8);
    for (const id of DESK_IDS) {
      const t = deskTypes[id];
      expect(t.label && t.tagline && t.nameLabel && t.namePlaceholder).toBeTruthy();
      expect(ACCENTS[t.accent].tint).toMatch(/^#/);
      expect(t.namePlaceholder).toMatch(/^e\.g\. your /); // never an invented business
    }
    expect(deskType("hotel").accent).toBe("amber");
    expect(deskType("fitness").accent).toBe("green");
  });

  it("falls back to custom for unknown ids", () => {
    expect(deskKey("clinic_receptionist")).toBe("custom");
    expect(deskType(undefined).label).toBe("Custom front desk");
  });

  it("takes an agent's type from the server first, then from what this browser remembers, then custom", () => {
    expect(deskKeyOfAgent({ id: "a1", template_id: "hotel" })).toBe("hotel");
    expect(deskKeyOfAgent({ id: "a2" })).toBe("custom");
    rememberDeskType("a2", "salon");
    expect(recalledDeskType("a2")).toBe("salon");
    expect(deskKeyOfAgent({ id: "a2" })).toBe("salon");
    expect(deskKeyOfAgent({ id: "a2", template_id: "clinic" })).toBe("clinic");
    rememberDeskType("a3", "not_a_desk");
    expect(deskKeyOfAgent({ id: "a3" })).toBe("custom");
    expect(deskKeyOfAgent(null)).toBe("custom");
  });

  it("the bookings icon reads template_id, ignores template and type guesses, and remembers only for agents without it", () => {
    window.localStorage.clear();
    expect(templateIdOf({ id: "b1", template_id: "hotel" })).toBe("hotel");
    expect(templateIdOf({ id: "b1", template_id: "hotel", template: "clinic", type: "salon" } as never)).toBe("hotel");
    expect(templateIdOf({ id: "b2", template: "clinic", type: "salon" } as never)).toBeNull();
    expect(templateIdOf({ id: "b3", template_id: null })).toBeNull();
    rememberDeskType("b3", "fitness");
    expect(templateIdOf({ id: "b3", template_id: null })).toBe("fitness");
    expect(templateIdOf({ id: "b3", template_id: "office" })).toBe("office");   // the server wins over the browser
  });

  it("survives blocked storage", () => {
    const spy = jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(deskKeyOfAgent({ id: "a9" })).toBe("custom");
    spy.mockRestore();
  });
});

describe("DeskPicker", () => {
  const options = [
    { id: "a1", name: "Ria", desk: "clinic" },
    { id: "a2", name: "Dev", desk: "hotel" },
    { id: "a3", name: "Mira", desk: undefined },
  ];

  it("shows the chosen option with its icon and opens a list of options with icons and type labels", () => {
    const onChange = jest.fn();
    const { container } = render(<DeskPicker label="Front desk" value="a1" options={options} onChange={onChange} />);
    const button = screen.getByRole("button", { name: /Front desk Ria/ });
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/illustrations/clinic-icon.svg");
    fireEvent.click(button);
    const rows = screen.getAllByRole("option");
    expect(rows.map((r) => r.textContent)).toEqual(["RiaClinic", "DevHotel", "MiraCustom front desk"]);
    expect(rows[1].querySelector("img")!.getAttribute("src")).toBe("/illustrations/hotel-icon.svg");
    expect(rows[2].querySelector("img")!.getAttribute("src")).toBe("/illustrations/custom-icon.svg");
    fireEvent.click(rows[1]);
    expect(onChange).toHaveBeenCalledWith("a2");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("works from the keyboard", () => {
    const onChange = jest.fn();
    render(<DeskPicker label="Front desk" value="a1" options={options} onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: "ArrowDown" });
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("a3");
    fireEvent.click(screen.getByRole("button"));
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("does not call onChange for the option already chosen", () => {
    const onChange = jest.fn();
    render(<DeskPicker label="Front desk" value="a1" options={options} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getAllByRole("option")[0]);
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("landing strip: For every front desk", () => {
  it("shows all eight scenes with a label under each, and the one line of copy", () => {
    const { container } = render(<ForEveryDesk />);
    expect(screen.getByRole("heading", { name: /For every front desk\./ })).toBeTruthy();
    expect(
      screen.getByText("Clinics, hotels, salons, restaurants, offices, institutes, service centres and gyms. Pick yours, name it, and it answers."),
    ).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual(DESK_IDS.map((id) => deskTypes[id].label));
    expect([...container.querySelectorAll("img")].map((i) => i.getAttribute("src"))).toEqual(DESK_IDS.map((id) => `/illustrations/${id}.svg`));
  });
});
