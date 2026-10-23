import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
const putMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  putBookingRules: (...args: unknown[]) => putMock(...args),
}));

import type { BookingRulesRead, BookingRulesResult } from "@/lib/api/client";
import { BookingRulesEditor } from "./booking-rules-editor";

const DEFAULTS: BookingRulesRead = {
  slot_interval_minutes: 15,
  min_notice_minutes: 120,
  max_days_ahead: 60,
  buffer_minutes: 0,
  customized: false,
};

const ok = (data: BookingRulesRead): BookingRulesResult => ({ kind: "ok", data });

describe("BookingRulesEditor", () => {
  beforeEach(() => {
    getTokenMock.mockReset().mockResolvedValue("token-123");
    putMock.mockReset();
  });

  it("shows the defaults, labelled as defaults, with whole-hour notice in hours", () => {
    render(<BookingRulesEditor result={ok(DEFAULTS)} />);

    expect(screen.getByRole("heading", { name: "Booking behavior" })).toBeInTheDocument();
    expect(screen.getByText("Using defaults")).toBeInTheDocument();
    expect(screen.getByLabelText("Appointment start times every")).toHaveValue("15");
    expect(screen.getByLabelText("Minimum notice")).toHaveValue(2);
    expect(screen.getByLabelText("Minimum notice unit")).toHaveValue("hours");
    expect(screen.getByLabelText("Book up to this many days ahead")).toHaveValue(60);
    expect(screen.getByLabelText("Buffer between appointments (minutes)")).toHaveValue(0);
    expect(screen.getByRole("button", { name: "Save booking rules" })).toBeEnabled();
  });

  it("hides the defaults notice and disables Save until a change when customized", () => {
    render(<BookingRulesEditor result={ok({ ...DEFAULTS, min_notice_minutes: 90, customized: true })} />);

    expect(screen.queryByText("Using defaults")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Minimum notice")).toHaveValue(90);
    expect(screen.getByLabelText("Minimum notice unit")).toHaveValue("minutes");
    expect(screen.getByRole("button", { name: "Save booking rules" })).toBeDisabled();
  });

  it("sends four integers (hours converted to minutes) and shows Saved only after the API confirms", async () => {
    let resolve!: (value: unknown) => void;
    putMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    render(<BookingRulesEditor result={ok(DEFAULTS)} />);

    await user.selectOptions(screen.getByLabelText("Appointment start times every"), "30");
    await user.clear(screen.getByLabelText("Minimum notice"));
    await user.type(screen.getByLabelText("Minimum notice"), "3");
    await user.clear(screen.getByLabelText("Book up to this many days ahead"));
    await user.type(screen.getByLabelText("Book up to this many days ahead"), "30");
    await user.clear(screen.getByLabelText("Buffer between appointments (minutes)"));
    await user.type(screen.getByLabelText("Buffer between appointments (minutes)"), "10");
    await user.click(screen.getByRole("button", { name: "Save booking rules" }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0]![1]).toEqual({
      slot_interval_minutes: 30,
      min_notice_minutes: 180,
      max_days_ahead: 30,
      buffer_minutes: 10,
    });
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();

    resolve({
      kind: "ok",
      data: { slot_interval_minutes: 30, min_notice_minutes: 180, max_days_ahead: 30, buffer_minutes: 10, customized: true },
    });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.queryByText("Using defaults")).not.toBeInTheDocument();
  });

  it("keeps a saved interval that is not a preset selectable and savable", async () => {
    putMock.mockResolvedValue({ kind: "ok", data: { ...DEFAULTS, slot_interval_minutes: 25, customized: true } });
    const user = userEvent.setup();
    render(<BookingRulesEditor result={ok({ ...DEFAULTS, slot_interval_minutes: 25, customized: true })} />);

    const select = screen.getByLabelText("Appointment start times every");
    expect(select).toHaveValue("25");
    expect(screen.getByRole("option", { name: "25 minutes" })).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Buffer between appointments (minutes)"));
    await user.type(screen.getByLabelText("Buffer between appointments (minutes)"), "5");
    await user.click(screen.getByRole("button", { name: "Save booking rules" }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0]![1]).toMatchObject({ slot_interval_minutes: 25, buffer_minutes: 5 });
  });

  it("validates before calling the API", async () => {
    const user = userEvent.setup();
    render(<BookingRulesEditor result={ok(DEFAULTS)} />);

    await user.clear(screen.getByLabelText("Book up to this many days ahead"));
    await user.type(screen.getByLabelText("Book up to this many days ahead"), "400");
    await user.clear(screen.getByLabelText("Buffer between appointments (minutes)"));
    await user.type(screen.getByLabelText("Buffer between appointments (minutes)"), "7");
    await user.clear(screen.getByLabelText("Minimum notice"));
    await user.type(screen.getByLabelText("Minimum notice"), "400");
    await user.click(screen.getByRole("button", { name: "Save booking rules" }));

    expect(await screen.findByText("Customers can book at most 365 days ahead.")).toBeInTheDocument();
    expect(screen.getByText("Use a multiple of 5 minutes.")).toBeInTheDocument();
    expect(screen.getByText("Notice can be at most 14 days (336 hours).")).toBeInTheDocument();
    expect(putMock).not.toHaveBeenCalled();
  });

  it("shows the API error and never Saved when the save fails", async () => {
    putMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    render(<BookingRulesEditor result={ok(DEFAULTS)} />);

    await user.click(screen.getByRole("button", { name: "Save booking rules" }));

    expect(await screen.findByText("Could not reach the API.")).toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    expect(screen.getByText("Using defaults")).toBeInTheDocument();
  });

  it("shows a server validation message", async () => {
    putMock.mockResolvedValue({ kind: "validation", message: "buffer_minutes is too large", fieldErrors: {} });
    const user = userEvent.setup();
    render(<BookingRulesEditor result={ok(DEFAULTS)} />);

    await user.click(screen.getByRole("button", { name: "Save booking rules" }));
    expect(await screen.findByText("buffer_minutes is too large")).toBeInTheDocument();
  });

  it("renders a section-only failure when the load failed", () => {
    render(<BookingRulesEditor result={{ kind: "unreachable", reason: "Could not reach the API." }} />);
    expect(screen.getByText("Can't load your booking rules")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save booking rules" })).not.toBeInTheDocument();
  });
});
