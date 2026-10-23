import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
const listServicesMock = vi.fn();
const getAvailabilityMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  listServices: (...args: unknown[]) => listServicesMock(...args),
  getAvailability: (...args: unknown[]) => getAvailabilityMock(...args),
}));

import type { Availability, AvailabilityReason, ServiceRead } from "@/lib/api/client";
import { AvailabilityPreview } from "./availability-preview";
import { formatSlotLabel } from "./availability-messages";

const service = (overrides: Partial<ServiceRead> = {}): ServiceRead => ({
  id: "svc-1",
  name: "Haircut",
  description: null,
  duration_minutes: 45,
  price: null,
  active: true,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  ...overrides,
});

const slot = (label: string) => ({
  start: `2026-10-23T${label}:00-04:00`,
  end: `2026-10-23T${label}:45:00-04:00`,
  label,
});

const day = (overrides: Partial<Availability> = {}): Availability => ({
  status: "available",
  date: "2026-10-23",
  timezone: "America/New_York",
  service: { id: "svc-1", name: "Haircut", duration_minutes: 45 },
  slots: [slot("09:00"), slot("09:15"), slot("14:00")],
  offered: [slot("09:00"), slot("14:00")],
  reason: null,
  ...overrides,
});

function servicesOk(services: ServiceRead[]) {
  listServicesMock.mockResolvedValue({ kind: "ok", data: { services, currency: "USD", max_services: 20 } });
}

async function check(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Check availability" }));
}

describe("AvailabilityPreview", () => {
  beforeEach(() => {
    getTokenMock.mockReset().mockResolvedValue("token-123");
    listServicesMock.mockReset();
    getAvailabilityMock.mockReset();
  });

  it("shows a loading state while services load", () => {
    listServicesMock.mockReturnValue(new Promise(() => {}));
    render(<AvailabilityPreview />);
    expect(screen.getByText("Loading your services")).toBeInTheDocument();
  });

  it("explains a failed services load and retries", async () => {
    listServicesMock.mockResolvedValueOnce({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    render(<AvailabilityPreview />);

    expect(await screen.findByText("Can't load your services")).toBeInTheDocument();
    servicesOk([service()]);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Service")).toBeInTheDocument();
  });

  it("shows an empty state when no service is active", async () => {
    servicesOk([service({ active: false })]);
    render(<AvailabilityPreview />);

    expect(await screen.findByText("No active services")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Agent Builder" })).toHaveAttribute("href", "/dashboard/agent");
    expect(screen.queryByRole("button", { name: "Check availability" })).not.toBeInTheDocument();
  });

  it("lists only active services and defaults the date to today", async () => {
    servicesOk([service(), service({ id: "svc-2", name: "Old cut", active: false })]);
    render(<AvailabilityPreview />);

    const select = await screen.findByLabelText("Service");
    expect(select).toHaveTextContent("Haircut (45 min)");
    expect(select).not.toHaveTextContent("Old cut");
    expect((screen.getByLabelText("Date") as HTMLInputElement).value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("checks the chosen service and date, then shows offered times in 12-hour format with the timezone", async () => {
    servicesOk([service(), service({ id: "svc-2", name: "Color", duration_minutes: 90 })]);
    getAvailabilityMock.mockResolvedValue({ kind: "ok", data: day() });
    const user = userEvent.setup();
    render(<AvailabilityPreview />);

    await user.selectOptions(await screen.findByLabelText("Service"), "svc-2");
    const date = screen.getByLabelText("Date");
    await user.clear(date);
    await user.type(date, "2026-10-23");
    await check(user);

    await waitFor(() => expect(getAvailabilityMock).toHaveBeenCalledWith("token-123", "svc-2", "2026-10-23"));
    expect(await screen.findByText("The receptionist would offer")).toBeInTheDocument();
    expect(screen.getByText("America/New_York")).toBeInTheDocument();
    // Offered 9:00 AM and 2:00 PM, plus the same two inside the all-slots list, and 9:15 only there.
    expect(screen.getAllByText("9:00 AM")).toHaveLength(2);
    expect(screen.getAllByText("2:00 PM")).toHaveLength(2);
    expect(screen.getAllByText("9:15 AM")).toHaveLength(1);
    expect(screen.getByText("All free start times (3)")).toBeInTheDocument();
  });

  it("shows a loading state while checking and disables the button", async () => {
    servicesOk([service()]);
    getAvailabilityMock.mockReturnValue(new Promise(() => {}));
    const user = userEvent.setup();
    render(<AvailabilityPreview />);

    await check(user);
    expect(await screen.findByText("Checking availability")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
  });

  it("clears an old result when the service changes", async () => {
    servicesOk([service(), service({ id: "svc-2", name: "Color" })]);
    getAvailabilityMock.mockResolvedValue({ kind: "ok", data: day() });
    const user = userEvent.setup();
    render(<AvailabilityPreview />);

    await check(user);
    expect(await screen.findByText("The receptionist would offer")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Service"), "svc-2");
    expect(screen.queryByText("The receptionist would offer")).not.toBeInTheDocument();
  });

  const NO_AVAILABILITY: [AvailabilityReason, string][] = [
    ["closed", "Closed that day."],
    ["hours_not_set", "Business hours aren't set yet"],
    ["fully_booked", "Fully booked"],
    ["no_time_left", "No time left that day"],
    ["past_date", "That date has already passed."],
    ["beyond_horizon", "further ahead than your booking rules allow"],
    ["service_too_long", "longer than the opening hours that day"],
    ["too_soon", "sooner than your minimum notice allows"],
  ];

  it.each(NO_AVAILABILITY)("explains no_availability reason %s without showing any time", async (reason, text) => {
    servicesOk([service()]);
    getAvailabilityMock.mockResolvedValue({
      kind: "ok",
      data: day({ status: "no_availability", slots: [], offered: [], reason }),
    });
    const user = userEvent.setup();
    render(<AvailabilityPreview />);

    await check(user);
    expect(await screen.findByText(new RegExp(text))).toBeInTheDocument();
    expect(screen.getByText("Nothing to offer")).toBeInTheDocument();
    expect(screen.queryByText(/\b(AM|PM)\b/)).not.toBeInTheDocument();
  });

  const CALENDAR_UNAVAILABLE: [AvailabilityReason, string][] = [
    ["not_connected", "No calendar is connected"],
    ["reconnect_needed", "needs to be reconnected"],
    ["unavailable", "Google Calendar couldn't be reached. Try again shortly."],
  ];

  it.each(CALENDAR_UNAVAILABLE)("explains calendar_unavailable reason %s", async (reason, text) => {
    servicesOk([service()]);
    getAvailabilityMock.mockResolvedValue({
      kind: "ok",
      data: day({ status: "calendar_unavailable", service: null, slots: [], offered: [], reason }),
    });
    const user = userEvent.setup();
    render(<AvailabilityPreview />);

    await check(user);
    expect(await screen.findByText(new RegExp(text))).toBeInTheDocument();
    expect(screen.getByText("Can't check the calendar")).toBeInTheDocument();
  });

  it("shows API failures as errors, never as times", async () => {
    servicesOk([service()]);
    getAvailabilityMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    render(<AvailabilityPreview />);

    await check(user);
    expect(await screen.findByText("Could not reach the API.")).toBeInTheDocument();
    expect(screen.queryByText("The receptionist would offer")).not.toBeInTheDocument();
  });

  it("explains a deleted service and a validation error", async () => {
    servicesOk([service()]);
    getAvailabilityMock.mockResolvedValueOnce({ kind: "service_not_found" });
    const user = userEvent.setup();
    render(<AvailabilityPreview />);

    await check(user);
    expect(await screen.findByText(/no longer exists/)).toBeInTheDocument();

    getAvailabilityMock.mockResolvedValueOnce({ kind: "validation", message: "date is invalid" });
    await check(user);
    expect(await screen.findByText("date is invalid")).toBeInTheDocument();
  });
});

describe("formatSlotLabel", () => {
  it.each([
    ["00:00", "12:00 AM"],
    ["09:05", "9:05 AM"],
    ["12:00", "12:00 PM"],
    ["14:30", "2:30 PM"],
    ["23:45", "11:45 PM"],
  ])("formats %s as %s", (label, expected) => {
    expect(formatSlotLabel(label)).toBe(expected);
  });

  it("returns an unrecognized label unchanged", () => {
    expect(formatSlotLabel("noon")).toBe("noon");
  });
});
