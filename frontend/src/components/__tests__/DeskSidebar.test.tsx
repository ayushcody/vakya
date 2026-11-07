import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { DeskSidebar, NAV, systemState } from "@/components/DeskSidebar";
import { PAGE_ACTIONS } from "@/components/CommandPalette";

let mockPath = "/desk";
let mockHealth: { data?: unknown; error?: unknown } = { data: { supabase: { configured: false } } };
jest.mock("next/navigation", () => ({ usePathname: () => mockPath, useRouter: () => ({ push: jest.fn(), replace: jest.fn() }) }));
jest.mock("@/components/AuthProvider", () => ({ useAuth: () => ({ user: null, signOut: jest.fn() }) }));
jest.mock("@/components/ThemeToggle", () => ({ ThemeToggle: () => null }));
jest.mock("@/hooks/useApi", () => ({ useHealth: () => mockHealth, useAgents: () => ({ data: [], isLoading: false }) }));

beforeEach(() => {
  mockPath = "/desk";
  mockHealth = { data: { supabase: { configured: false } } };
});

async function open(path = "/desk") {
  mockPath = path;
  const view = render(<DeskSidebar />);
  await act(async () => {});
  return view;
}

const desktop = () => document.querySelector("aside") as HTMLElement;
const mainNav = () => within(desktop()).getByRole("navigation", { name: "Main" });
const linkRows = (root: HTMLElement) => within(root).getAllByRole("link").map((a) => [a.textContent?.trim(), a.getAttribute("href")]);

test("the desk: five daily pages and Settings at the foot, each with its own icon", async () => {
  await open("/desk");
  expect(linkRows(mainNav())).toEqual([
    ["Today", "/desk"],
    ["Bookings", "/desk/bookings"],
    ["Calls", "/desk/calls"],
    ["Front desk", "/desk/receptionist"],
    ["Proof", "/desk/proof"],
    ["Settings", "/desk/settings"],
  ]);
  const icons = within(mainNav()).getAllByRole("link").map((a) => a.querySelector("svg")?.innerHTML);
  expect(new Set(icons).size).toBe(icons.length);
});

test("a page opened from an item marks that item: This week under Today, Callers under Calls, Report card under Proof", async () => {
  for (const [path, item] of [["/desk/week", "Today"], ["/desk/callers", "Calls"], ["/desk/report-card", "Proof"], ["/desk/try", "Front desk"]]) {
    const { unmount } = await open(path);
    expect(within(mainNav()).getByRole("link", { name: item })).toHaveAttribute("aria-current", "page");
    unmount();
  }
});

test("there is no area switch and nothing from the old platform", async () => {
  await open("/desk");
  expect(within(desktop()).queryByRole("radiogroup")).toBeNull();
  for (const gone of ["Studio", "Voice Studio", "Say it once", "Campaigns", "Approvals", "API keys", "Results", "Safety check", "Talk to your agent"]) {
    expect(within(desktop()).queryByText(gone)).toBeNull();
  }
});

test("the command palette lists the same pages as the sidebar", () => {
  const pages = PAGE_ACTIONS.filter((a) => a.hint === "Page").map((a) => a.href);
  for (const item of NAV) expect(pages).toContain(item.href);
});

test("Today is current only on /desk itself; a deeper page marks its own item", async () => {
  const { unmount } = await open("/desk");
  expect(within(mainNav()).getByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
  unmount();
  await open("/desk/calls");
  expect(within(mainNav()).getByRole("link", { name: "Calls" })).toHaveAttribute("aria-current", "page");
  expect(within(mainNav()).getByRole("link", { name: "Today" })).not.toHaveAttribute("aria-current");
});

test("the mobile drawer opens from the top bar, lists the same pages and closes", async () => {
  await open("/desk");
  fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
  const drawer = screen.getByRole("dialog", { name: "Navigation" });
  expect(within(drawer).getAllByRole("link", { name: /^(Today|Bookings|Calls|Front desk|Proof|Settings)$/ })).toHaveLength(6);
  fireEvent.click(within(drawer).getByRole("button", { name: "Close menu" }));
  await act(async () => {});
  expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
});

test("the sidebar can collapse to icons, which keep their names", async () => {
  const onToggle = jest.fn();
  render(<DeskSidebar collapsed onToggleCollapsed={onToggle} />);
  const link = within(desktop()).getByRole("link", { name: "Front desk" });
  expect(link).toHaveAttribute("title", "Front desk");
  fireEvent.click(within(desktop()).getByRole("button", { name: "Expand sidebar" }));
  expect(onToggle).toHaveBeenCalled();
});

test("one status line says Ready once the server answers", async () => {
  await open();
  expect(within(desktop()).getByRole("status")).toHaveTextContent("Ready");
});

test("a server that does not answer needs attention", async () => {
  mockHealth = { data: undefined, error: new Error("offline") };
  await open();
  expect(within(desktop()).getByRole("status")).toHaveTextContent("Needs attention");
});

test("system state: connecting is starting, an answer is ready, no answer is attention", () => {
  expect(systemState(false, true)).toBe("ready");
  expect(systemState(false, false)).toBe("starting");
  expect(systemState(true, false)).toBe("attention");
});
