import { render, screen } from "@testing-library/react";
import { MobileTabBar } from "@/components/phone/MobileTabBar";

let path = "/desk";
jest.mock("next/navigation", () => ({ usePathname: () => path }));

test("the phone tab bar has the four everyday pages and marks the current one", () => {
  path = "/desk/bookings/A12";
  render(<MobileTabBar />);
  const nav = screen.getByRole("navigation", { name: "Quick pages" });
  const links = Array.from(nav.querySelectorAll("a")).map((a) => [a.textContent, a.getAttribute("href")]);
  expect(links).toEqual([["Today", "/desk"], ["Bookings", "/desk/bookings"], ["Calls", "/desk/calls"], ["Front desk", "/desk/receptionist"]]);
  expect(screen.getByRole("link", { name: "Bookings" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Today" })).not.toHaveAttribute("aria-current");
});
