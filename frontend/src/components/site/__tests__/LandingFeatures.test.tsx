import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ScheduleVisualiser } from "../ScheduleVisualiser";
import { FEATURES, WhatVakyaDoes } from "../WhatVakyaDoes";

describe("WhatVakyaDoes", () => {
  it("lists every feature and the illustrative documents strip", () => {
    render(<WhatVakyaDoes />);
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(FEATURES.length);
    expect(screen.getByText(/Hindi, Marathi, English and code-mixed/)).toBeInTheDocument();
    expect(screen.getByTestId("docs-to-answer")).toBeInTheDocument();
  });
});

describe("ScheduleVisualiser", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("renders the grid with labelled slots", () => {
    render(<ScheduleVisualiser />);
    expect(screen.getByRole("button", { name: "Mon 9 am, open. Press to close." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mon 10 am, booked." })).toHaveAttribute("aria-disabled", "true");
  });

  it("toggles a slot with the keyboard", async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    render(<ScheduleVisualiser />);
    const slot = screen.getByRole("button", { name: "Tue 9 am, open. Press to close." });
    slot.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Tue 9 am, closed. Press to open." })).toHaveAttribute("data-state", "closed");
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Tue 9 am, open. Press to close." })).toBeInTheDocument();
  });

  it("books a slot by phone after a moment and does not toggle booked slots", () => {
    render(<ScheduleVisualiser />);
    act(() => {
      jest.advanceTimersByTime(2500);
    });
    expect(screen.getByRole("button", { name: "Wed 11 am, booked by phone." })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Wed 11 am, booked by phone." }));
    expect(screen.getByRole("button", { name: "Wed 11 am, booked by phone." })).toHaveAttribute("data-state", "phone");
    expect(screen.getByTestId("schedule-caption")).toHaveTextContent("Wed 11 am");
  });
});
