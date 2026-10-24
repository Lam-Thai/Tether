import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { BookingRead, BookingStatus } from "@/lib/api/client";
import { BookingsPanel, formatBookingDate } from "./bookings-panel";

const booking = (status: BookingStatus, overrides: Partial<BookingRead> = {}): BookingRead => ({
  id: `b-${status}`,
  service_name: "Haircut",
  start: "2026-10-09T15:30:00-04:00",
  end: "2026-10-09T16:15:00-04:00",
  date: "2026-10-09",
  label: "15:30",
  customer_name: "Alex",
  status,
  ...overrides,
});

describe("BookingsPanel", () => {
  it("explains there is no chat yet", () => {
    render(<BookingsPanel state={{ kind: "idle" }} onRetry={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Bookings in this chat" })).toBeInTheDocument();
    expect(screen.getByText(/No chat yet/)).toBeInTheDocument();
  });

  it("shows an empty state", () => {
    render(<BookingsPanel state={{ kind: "ok", bookings: [] }} onRetry={vi.fn()} />);
    expect(screen.getByText(/No bookings yet/)).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("shows a loading status", () => {
    render(<BookingsPanel state={{ kind: "loading" }} onRetry={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading bookings");
  });

  it("shows a failure with retry", async () => {
    const onRetry = vi.fn();
    render(<BookingsPanel state={{ kind: "error", message: "Could not reach the API." }} onRetry={onRetry} />);
    expect(screen.getByText("Bookings did not load")).toBeInTheDocument();
    expect(screen.getByText("Could not reach the API.")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("lists a confirmed booking with service, local date, 12-hour time and customer", () => {
    render(<BookingsPanel state={{ kind: "ok", bookings: [booking("confirmed")] }} onRetry={vi.fn()} />);
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent("Confirmed");
    expect(item).toHaveTextContent("Haircut");
    expect(item).toHaveTextContent("Fri, Oct 9, 3:30 PM");
    expect(item).toHaveTextContent("For Alex");
  });

  it.each([
    ["pending", "In progress"],
    ["failed", "Not booked"],
    ["needs_reconciliation", "Needs review"],
    ["cancelled", "Cancelled"],
  ] as const)("labels %s as %s and never as confirmed", (status, label) => {
    render(<BookingsPanel state={{ kind: "ok", bookings: [booking(status)] }} onRetry={vi.fn()} />);
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent(label);
    expect(item).not.toHaveTextContent("Confirmed");
  });

  it("explains that a needs-review booking may be on the calendar", () => {
    render(
      <BookingsPanel state={{ kind: "ok", bookings: [booking("needs_reconciliation")] }} onRetry={vi.fn()} />,
    );
    expect(screen.getByText(/may already be on your calendar/)).toBeInTheDocument();
  });
});

describe("formatBookingDate", () => {
  it("formats the business-local date without shifting it", () => {
    expect(formatBookingDate("2026-10-09")).toBe("Fri, Oct 9");
    expect(formatBookingDate("2026-01-01")).toBe("Thu, Jan 1");
  });

  it("returns an unexpected value unchanged", () => {
    expect(formatBookingDate("soon")).toBe("soon");
  });
});
