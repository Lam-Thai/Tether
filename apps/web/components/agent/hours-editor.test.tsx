import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
const putMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  putBusinessHours: (...args: unknown[]) => putMock(...args),
}));

import type { BusinessHours, BusinessHoursResult, DayHours } from "@/lib/api/client";
import { HoursEditor } from "./hours-editor";

const WEEK: DayHours[] = Array.from({ length: 7 }, (_, i) =>
  i < 5
    ? { day_of_week: i, is_closed: false, opens_at: "08:30:00", closes_at: "17:00:00" }
    : { day_of_week: i, is_closed: true, opens_at: null, closes_at: null },
);

function configured(days = WEEK): BusinessHoursResult {
  return { kind: "ok", data: { timezone: "America/New_York", configured: true, days } };
}
const NOT_SET: BusinessHoursResult = {
  kind: "ok",
  data: { timezone: "America/New_York", configured: false, days: [] },
};

describe("HoursEditor", () => {
  beforeEach(() => {
    getTokenMock.mockReset().mockResolvedValue("token-123");
    putMock.mockReset();
  });

  it("renders seven rows Monday to Sunday with the timezone", () => {
    render(<HoursEditor result={configured()} />);

    expect(screen.getAllByRole("switch")).toHaveLength(7);
    expect(screen.getByLabelText("Monday opens")).toHaveValue("08:30");
    expect(screen.getByLabelText("Friday closes")).toHaveValue("17:00");
    expect(screen.getByRole("switch", { name: "Closed: Saturday" })).toBeChecked();
    expect(screen.queryByLabelText("Saturday opens")).not.toBeInTheDocument();
    expect(screen.getByText("America/New_York")).toBeInTheDocument();
    expect(screen.queryByText("Not set yet")).not.toBeInTheDocument();
  });

  it("shows a clearly unsaved default draft when hours were never set", () => {
    render(<HoursEditor result={NOT_SET} />);

    expect(screen.getByText("Not set yet")).toBeInTheDocument();
    expect(screen.getByLabelText("Monday opens")).toHaveValue("09:00");
    expect(screen.getByRole("switch", { name: "Closed: Sunday" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save hours" })).toBeEnabled();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("keeps Save disabled until something changes when hours exist", () => {
    render(<HoursEditor result={configured()} />);
    expect(screen.getByRole("button", { name: "Save hours" })).toBeDisabled();
  });

  it("clears the times when a day is marked closed", async () => {
    putMock.mockResolvedValue({ kind: "ok", data: { timezone: "UTC", configured: true, days: WEEK } });
    const user = userEvent.setup();
    render(<HoursEditor result={configured()} />);

    await user.click(screen.getByRole("switch", { name: "Closed: Monday" }));
    expect(screen.queryByLabelText("Monday opens")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save hours" }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const days = putMock.mock.calls[0]![1] as DayHours[];
    expect(days[0]).toEqual({ day_of_week: 0, is_closed: true, opens_at: null, closes_at: null });
  });

  it("sends the whole week with HH:MM:SS times and shows Saved only after the API confirms", async () => {
    let resolve: (value: unknown) => void = () => {};
    putMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    render(<HoursEditor result={NOT_SET} />);

    await user.click(screen.getByRole("button", { name: "Save hours" }));

    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(1));
    const [token, days] = putMock.mock.calls[0]! as [string, DayHours[]];
    expect(token).toBe("token-123");
    expect(days).toHaveLength(7);
    expect(days.map((d) => d.day_of_week)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(days[0]).toEqual({ day_of_week: 0, is_closed: false, opens_at: "09:00:00", closes_at: "17:00:00" });
    expect(days[6]).toEqual({ day_of_week: 6, is_closed: true, opens_at: null, closes_at: null });
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();

    const saved: BusinessHours = { timezone: "America/New_York", configured: true, days };
    resolve({ kind: "ok", data: saved });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.queryByText("Not set yet")).not.toBeInTheDocument();
  });

  it("blocks Save with a per-row error when opens is not before closes", async () => {
    const user = userEvent.setup();
    render(<HoursEditor result={configured()} />);

    fireEvent.change(screen.getByLabelText("Tuesday opens"), { target: { value: "18:00" } });
    await user.click(screen.getByRole("button", { name: "Save hours" }));

    expect(await screen.findByText("Closing time must be after opening time.")).toBeInTheDocument();
    expect(screen.getByLabelText("Tuesday opens")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Monday opens")).not.toHaveAttribute("aria-invalid");
    expect(putMock).not.toHaveBeenCalled();
  });

  it("blocks Save when an open day is missing a time", async () => {
    const user = userEvent.setup();
    render(<HoursEditor result={configured()} />);

    fireEvent.change(screen.getByLabelText("Monday closes"), { target: { value: "" } });
    await user.click(screen.getByRole("button", { name: "Save hours" }));

    expect(await screen.findByText(/Set both opening and closing times/)).toBeInTheDocument();
    expect(putMock).not.toHaveBeenCalled();
  });

  it("shows an API failure without claiming it saved", async () => {
    putMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    render(<HoursEditor result={NOT_SET} />);

    await user.click(screen.getByRole("button", { name: "Save hours" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the API.");
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    expect(screen.getByText("Not set yet")).toBeInTheDocument();

    // Editing a time clears the stale error.
    fireEvent.change(screen.getByLabelText("Tuesday opens"), { target: { value: "08:00" } });
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("shows a 422 message from the API", async () => {
    putMock.mockResolvedValue({ kind: "validation", message: "Hours are invalid.", fieldErrors: {} });
    const user = userEvent.setup();
    render(<HoursEditor result={NOT_SET} />);

    await user.click(screen.getByRole("button", { name: "Save hours" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Hours are invalid.");
  });

  it("shows load-failure callouts", () => {
    const { unmount } = render(
      <HoursEditor result={{ kind: "unreachable", reason: "Could not reach the API." }} />,
    );
    expect(screen.getByText("Can't load your business hours")).toBeInTheDocument();
    unmount();
    render(<HoursEditor result={{ kind: "subscription_required" }} />);
    expect(screen.getByRole("link", { name: "Choose plan" })).toBeInTheDocument();
  });
});
