import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { CalendarOption, CalendarStatus } from "@/lib/api/client";

const getTokenMock = vi.fn();
const getCalendarConnectUrlMock = vi.fn();
const listCalendarsMock = vi.fn();
const selectCalendarMock = vi.fn();
const disconnectCalendarMock = vi.fn();
const listServicesMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  getCalendarConnectUrl: (...args: unknown[]) => getCalendarConnectUrlMock(...args),
  listCalendars: (...args: unknown[]) => listCalendarsMock(...args),
  selectCalendar: (...args: unknown[]) => selectCalendarMock(...args),
  disconnectCalendar: (...args: unknown[]) => disconnectCalendarMock(...args),
  listServices: (...args: unknown[]) => listServicesMock(...args),
  getAvailability: vi.fn(),
}));

import { CalendarPanel } from "./calendar-panel";

const assignMock = vi.fn();
const originalLocation = window.location;

const status = (overrides: Partial<CalendarStatus> = {}): CalendarStatus => ({
  status: "not_connected",
  calendar_id: null,
  calendar_name: null,
  last_error: null,
  last_synced_at: null,
  ...overrides,
});

const connected = status({ status: "connected", calendar_id: "cal-1", calendar_name: "Salon bookings" });
const needsSelection = status({ status: "needs_selection" });
const SALON = 'Connected · "Salon bookings"';

const CALENDARS: CalendarOption[] = [
  { id: "cal-1", name: "Salon bookings", primary: false, time_zone: "Europe/London" },
  { id: "cal-2", name: "Lam Thai", primary: true, time_zone: "Europe/London" },
];

beforeEach(() => {
  // The native <dialog> modal API is not implemented in happy-dom; fake the open flag only.
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  getTokenMock.mockReset().mockResolvedValue("token-123");
  getCalendarConnectUrlMock.mockReset();
  listCalendarsMock.mockReset();
  selectCalendarMock.mockReset();
  disconnectCalendarMock.mockReset();
  listServicesMock.mockReset().mockResolvedValue({ kind: "ok", data: { services: [], currency: "USD", max_services: 20 } });
  assignMock.mockReset();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, assign: assignMock },
  });
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
});

async function openDisconnectDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Disconnect" }));
  return screen.findByRole("dialog", { name: "Disconnect calendar?" });
}

describe("CalendarPanel: load failures", () => {
  it("shows an error Callout when the status can't be loaded", () => {
    render(<CalendarPanel status={{ kind: "unreachable", reason: "Could not reach the API." }} />);
    expect(screen.getByRole("heading", { level: 1, name: "Can't load your calendar" })).toBeInTheDocument();
    expect(screen.getByText(/Could not reach the API\./)).toBeInTheDocument();
  });

  it("explains an unconfigured server instead of offering Connect", () => {
    render(<CalendarPanel status={{ kind: "not_configured" }} />);
    expect(screen.getByText(/isn't set up on this server yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect Google Calendar" })).not.toBeInTheDocument();
  });

  it("shows signed-out and plan-required backstops", () => {
    const { rerender } = render(<CalendarPanel status={{ kind: "unauthorized" }} />);
    expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/sign-in");
    rerender(<CalendarPanel status={{ kind: "subscription_required" }} />);
    expect(screen.getByRole("link", { name: "Choose plan" })).toHaveAttribute("href", "/billing/plan");
  });
});

describe("CalendarPanel: not connected", () => {
  it("offers Connect and follows the Google URL the API returns", async () => {
    getCalendarConnectUrlMock.mockResolvedValue({
      kind: "ok",
      url: "https://accounts.google.com/o/oauth2/v2/auth?x=1",
    });
    render(<CalendarPanel status={{ kind: "ok", data: status() }} />);

    expect(screen.getByText("No calendar connected")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Connect Google Calendar" }));

    await waitFor(() =>
      expect(assignMock).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/v2/auth?x=1"),
    );
    expect(getCalendarConnectUrlMock).toHaveBeenCalledWith("token-123");
    expect(screen.getByRole("button", { name: "Opening Google…" })).toBeDisabled();
  });

  it("re-enables the button with an inline error when the call throws", async () => {
    getCalendarConnectUrlMock.mockRejectedValue(new Error("boom"));
    render(<CalendarPanel status={{ kind: "ok", data: status() }} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Connect Google Calendar" }));

    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect Google Calendar" })).toBeEnabled();
    expect(assignMock).not.toHaveBeenCalled();
  });

  it("shows the failure and does not navigate when Connect fails", async () => {
    getCalendarConnectUrlMock.mockResolvedValue({ kind: "invalid_url" });
    render(<CalendarPanel status={{ kind: "ok", data: status() }} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Connect Google Calendar" }));

    expect(await screen.findByText(/wasn't a Google link/)).toBeInTheDocument();
    expect(assignMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Connect Google Calendar" })).toBeEnabled();
  });
});

describe("CalendarPanel: needs selection", () => {
  it("shows a loading state, then the picker, and saves only after the API confirms", async () => {
    let resolveList: (value: unknown) => void = () => {};
    listCalendarsMock.mockReturnValue(new Promise((resolve) => (resolveList = resolve)));
    selectCalendarMock.mockResolvedValue({ kind: "ok", data: connected });
    render(<CalendarPanel status={{ kind: "ok", data: needsSelection }} />);

    expect(await screen.findByText("Loading your calendars")).toBeInTheDocument();
    resolveList({ kind: "ok", calendars: CALENDARS });

    const select = await screen.findByLabelText("Calendar for bookings");
    const save = screen.getByRole("button", { name: "Save calendar" });
    expect(save).toBeDisabled();
    expect(screen.getByRole("option", { name: "Lam Thai (primary)" })).toBeInTheDocument();

    const user = userEvent.setup();
    await user.selectOptions(select, "cal-1");
    expect(screen.queryByText(SALON)).not.toBeInTheDocument();
    await user.click(save);

    expect(selectCalendarMock).toHaveBeenCalledWith("token-123", "cal-1");
    expect(await screen.findByText(SALON)).toBeInTheDocument();
  });

  it("keeps the picker and shows the error when saving fails", async () => {
    listCalendarsMock.mockResolvedValue({ kind: "ok", calendars: CALENDARS });
    selectCalendarMock.mockResolvedValue({ kind: "calendar_not_found" });
    render(<CalendarPanel status={{ kind: "ok", data: needsSelection }} />);

    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Calendar for bookings"), "cal-2");
    await user.click(screen.getByRole("button", { name: "Save calendar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no longer available");
    expect(screen.queryByText(/^Connected/)).not.toBeInTheDocument();
  });

  it("shows a retry when the list fails", async () => {
    listCalendarsMock
      .mockResolvedValueOnce({ kind: "unavailable" })
      .mockResolvedValueOnce({ kind: "ok", calendars: CALENDARS });
    render(<CalendarPanel status={{ kind: "ok", data: needsSelection }} />);

    expect(await screen.findByText("Can't load your calendars")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Calendar for bookings")).toBeInTheDocument();
  });

  it("shows an empty state when the account owns no calendars", async () => {
    listCalendarsMock.mockResolvedValue({ kind: "ok", calendars: [] });
    render(<CalendarPanel status={{ kind: "ok", data: needsSelection }} />);
    expect(await screen.findByText("No calendars you own")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save calendar" })).not.toBeInTheDocument();
  });

  it("moves to Reconnect needed when the list says access is gone", async () => {
    listCalendarsMock.mockResolvedValue({ kind: "reconnect_needed" });
    render(<CalendarPanel status={{ kind: "ok", data: needsSelection }} />);
    expect(await screen.findByText("Reconnect needed")).toBeInTheDocument();
  });

  it("says when the previously chosen calendar disappeared", async () => {
    listCalendarsMock.mockResolvedValue({ kind: "ok", calendars: CALENDARS });
    render(
      <CalendarPanel
        status={{ kind: "ok", data: status({ status: "needs_selection", last_error: "calendar_missing" }) }}
      />,
    );
    expect(await screen.findByText(/no longer in your account/)).toBeInTheDocument();
  });
});

describe("CalendarPanel: connected", () => {
  it("shows the confirmed calendar name with Change and Disconnect", () => {
    render(<CalendarPanel status={{ kind: "ok", data: connected }} />);
    expect(screen.getByText(SALON)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change calendar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
  });

  it("changes the calendar through the picker and can cancel", async () => {
    listCalendarsMock.mockResolvedValue({ kind: "ok", calendars: CALENDARS });
    selectCalendarMock.mockResolvedValue({
      kind: "ok",
      data: { ...connected, calendar_id: "cal-2", calendar_name: "Lam Thai" },
    });
    render(<CalendarPanel status={{ kind: "ok", data: connected }} />);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Change calendar" }));
    expect(await screen.findByLabelText("Calendar for bookings")).toHaveValue("cal-1");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText(SALON)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Change calendar" }));
    await user.selectOptions(await screen.findByLabelText("Calendar for bookings"), "cal-2");
    await user.click(screen.getByRole("button", { name: "Save calendar" }));
    expect(await screen.findByText('Connected · "Lam Thai"')).toBeInTheDocument();
  });

  it("flags a failed last check without dropping the connection", () => {
    render(<CalendarPanel status={{ kind: "ok", data: { ...connected, last_error: "unavailable" } }} />);
    expect(screen.getByText(SALON)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("couldn't reach Google Calendar");
  });

  it("asks for confirmation before disconnecting, and Cancel changes nothing", async () => {
    render(<CalendarPanel status={{ kind: "ok", data: connected }} />);
    const user = userEvent.setup();
    const dialog = await openDisconnectDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(disconnectCalendarMock).not.toHaveBeenCalled();
    expect(screen.getByText(SALON)).toBeInTheDocument();
  });

  it("disconnects after confirmation and reports a confirmed revoke", async () => {
    disconnectCalendarMock.mockResolvedValue({ kind: "ok", revoked: true });
    render(<CalendarPanel status={{ kind: "ok", data: connected }} />);
    const user = userEvent.setup();
    const dialog = await openDisconnectDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    expect(await screen.findByText("Calendar disconnected")).toBeInTheDocument();
    expect(disconnectCalendarMock).toHaveBeenCalledWith("token-123");
    expect(screen.getByRole("button", { name: "Connect Google Calendar" })).toBeInTheDocument();
    expect(screen.queryByText(SALON)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /permissions/ })).not.toBeInTheDocument();
  });

  it("warns with a link when Google did not confirm the revoke", async () => {
    disconnectCalendarMock.mockResolvedValue({ kind: "ok", revoked: false });
    render(<CalendarPanel status={{ kind: "ok", data: connected }} />);
    const user = userEvent.setup();
    const dialog = await openDisconnectDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    const link = await screen.findByRole("link", { name: "Google account permissions page" });
    expect(link).toHaveAttribute("href", "https://myaccount.google.com/permissions");
    expect(screen.queryByText("Calendar disconnected")).not.toBeInTheDocument();
  });

  it("stays connected and shows the error when disconnect fails", async () => {
    disconnectCalendarMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    render(<CalendarPanel status={{ kind: "ok", data: connected }} />);
    const user = userEvent.setup();
    const dialog = await openDisconnectDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Could not reach the API.");
    expect(screen.getByText(SALON)).toBeInTheDocument();
  });
});

describe("CalendarPanel: reconnect needed", () => {
  it("shows Reconnect needed with a Reconnect action that starts the connect flow", async () => {
    getCalendarConnectUrlMock.mockResolvedValue({ kind: "ok", url: "https://accounts.google.com/auth" });
    render(
      <CalendarPanel
        status={{ kind: "ok", data: status({ status: "reconnect_needed", last_error: "auth_revoked" }) }}
      />,
    );

    expect(screen.getByText("Reconnect needed")).toBeInTheDocument();
    expect(screen.queryByText(/^Connected/)).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => expect(assignMock).toHaveBeenCalledWith("https://accounts.google.com/auth"));
  });
});

describe("CalendarPanel: availability preview", () => {
  it("offers the preview only for a connected calendar", async () => {
    const { unmount } = render(<CalendarPanel status={{ kind: "ok", data: connected }} />);
    expect(await screen.findByText("No active services")).toBeInTheDocument();

    unmount();
    render(<CalendarPanel status={{ kind: "ok", data: status() }} />);
    expect(screen.queryByText("No active services")).not.toBeInTheDocument();
    expect(screen.getByText(/Connect a calendar and choose which one to use first/)).toBeInTheDocument();
  });
});
