import { act, fireEvent, render, screen } from "@testing-library/react";
import { WatchPreview } from "@/components/watch/WatchPreview";
import { FILM, MOMENTS, MORE_FILMS, clock } from "@/components/watch/moments";

jest.mock("next/image", () => ({
  __esModule: true,
  // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text
  default: (p: { src: string; alt: string }) => <img src={p.src} alt={p.alt} />,
}));

beforeAll(() => {
  window.matchMedia = jest.fn().mockReturnValue({ matches: false, addEventListener: jest.fn(), removeEventListener: jest.fn() });
  HTMLMediaElement.prototype.play = jest.fn().mockResolvedValue(undefined);
  HTMLMediaElement.prototype.pause = jest.fn();
});

beforeEach(() => localStorage.clear());

test("the chapters run in order inside the film and read as clock times", () => {
  const at = MOMENTS.map((m) => m.at);
  expect([...at].sort((a, b) => a - b)).toEqual(at);
  expect(at[at.length - 1]).toBeLessThan(FILM.seconds);
  expect(new Set(MOMENTS.map((m) => m.id)).size).toBe(MOMENTS.length);
  expect(clock(0)).toBe("0:00");
  expect(clock(125)).toBe("2:05");
  expect(clock(FILM.seconds)).toBe("3:04");
});

test("the title page offers Play, the moments and the site, and Play opens the player with a violet progress line", () => {
  render(<WatchPreview />);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/Call once,\s*it’s booked\./);
  expect(screen.getAllByRole("link", { name: /Visit the site/ })[0]).toHaveAttribute("href", "/home");
  expect(screen.getAllByRole("button", { name: /^Play from / })).toHaveLength(MOMENTS.length);

  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  const player = screen.getByRole("dialog", { name: "Vakya demo film" });
  expect(player.querySelector("video")).toHaveAttribute("src", FILM.src);
  expect(screen.getByRole("slider", { name: "Seek" })).toBeInTheDocument();
  const fills = Array.from(player.querySelectorAll(".watch-track > div")).map((d) => d.className);
  expect(fills.some((c) => c.includes("bg-[var(--s-bronze)]"))).toBe(true);        // the played part is the violet accent

  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a moment opens the player at its own time", () => {
  render(<WatchPreview />);
  const readback = MOMENTS.find((m) => m.id === "readback")!;
  fireEvent.click(screen.getByRole("button", { name: `Play from ${clock(readback.at)}: ${readback.title}` }));
  const v = screen.getByRole("dialog").querySelector("video")!;
  expect(v.currentTime).toBe(readback.at);
});

test("where a visitor stopped comes back as Resume", () => {
  localStorage.setItem("vakya-demo-position", "40");
  render(<WatchPreview />);
  expect(screen.getByRole("button", { name: "Resume from 0:40" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /From the start/ })).toBeInTheDocument();
});

test("More films opens the animated real case and the short film in the same player, without touching the demo's resume", () => {
  render(<WatchPreview />);
  expect(screen.getByRole("heading", { name: "More films" })).toBeInTheDocument();
  for (const f of MORE_FILMS) {
    fireEvent.click(screen.getByRole("button", { name: `Play ${f.title}, ${clock(f.seconds)}` }));
    const player = screen.getByRole("dialog", { name: f.label });
    const v = player.querySelector("video")!;
    expect(v).toHaveAttribute("src", f.src);
    expect(v).toHaveAttribute("poster", f.poster);
    expect(v.currentTime).toBe(0);
    v.currentTime = 30;
    fireEvent.pause(v);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  }
  expect(localStorage.getItem("vakya-demo-position")).toBeNull();
  expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
});

test("the films' listed lengths are the files' own, and their ids are unique", () => {
  expect(MORE_FILMS.map((f) => clock(f.seconds))).toEqual(["2:12", "0:59"]);
  expect(new Set([FILM.id, ...MORE_FILMS.map((f) => f.id)]).size).toBe(1 + MORE_FILMS.length);
});
