import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const replaceMock = vi.fn();
const getTokenMock = vi.fn();
const completeMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  completeCalendarConnection: (...args: unknown[]) => completeMock(...args),
}));

import { CalendarCallback } from "./calendar-callback";

const CALLBACK = "/dashboard/settings/calendar/callback";
const SETTINGS = "/dashboard/settings/calendar";

beforeEach(() => {
  replaceMock.mockReset();
  getTokenMock.mockReset().mockResolvedValue("token-123");
  completeMock.mockReset();
});

describe("CalendarCallback", () => {
  it("shows a message and makes no API call when Google reports access_denied", async () => {
    render(<CalendarCallback code={null} state="s" error="access_denied" />);

    expect(screen.getByRole("heading", { level: 1, name: "Calendar not connected" })).toBeInTheDocument();
    expect(screen.getByText(/didn't allow access in Google/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Try again" })).toHaveAttribute("href", SETTINGS);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith(CALLBACK));
    expect(completeMock).not.toHaveBeenCalled();
    expect(getTokenMock).not.toHaveBeenCalled();
  });

  it("does not echo an unknown Google error value", () => {
    render(<CalendarCallback code={null} state={null} error="<b>weird</b>" />);
    expect(screen.queryByText(/weird/)).not.toBeInTheDocument();
    expect(screen.getByText(/Google reported a problem/)).toBeInTheDocument();
  });

  it("makes no API call when code or state is missing", () => {
    render(<CalendarCallback code="c" state={null} error={null} />);
    expect(screen.getByText(/details Google sends/)).toBeInTheDocument();
    expect(completeMock).not.toHaveBeenCalled();
  });

  it("strips the one-time values, posts once even under strict mode, then returns to settings", async () => {
    completeMock.mockResolvedValue({
      kind: "ok",
      data: {
        status: "needs_selection",
        calendar_id: null,
        calendar_name: null,
        last_error: null,
        last_synced_at: null,
      },
    });
    render(
      <StrictMode>
        <CalendarCallback code="one-time-code" state="signed-state" error={null} />
      </StrictMode>,
    );

    expect(screen.getByRole("heading", { name: "Connecting your calendar" })).toBeInTheDocument();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith(SETTINGS));

    expect(completeMock).toHaveBeenCalledTimes(1);
    expect(completeMock).toHaveBeenCalledWith("token-123", "one-time-code", "signed-state");
    // The address bar is cleaned before the post resolves.
    expect(replaceMock.mock.calls[0]).toEqual([CALLBACK]);
  });

  it("does not claim success before the API confirms", async () => {
    completeMock.mockReturnValue(new Promise(() => {}));
    render(<CalendarCallback code="c" state="s" error={null} />);
    await waitFor(() => expect(completeMock).toHaveBeenCalled());
    expect(screen.queryByText("Google account connected")).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalledWith(SETTINGS);
  });

  it.each([
    ["invalid_state", /expired or doesn't match/],
    ["scopes_missing", /leave every box ticked/],
    ["grant_incomplete", /lasting access/],
    ["unavailable", /can't be reached/],
  ])("shows a mapped error on %s and does not retry", async (kind, pattern) => {
    completeMock.mockResolvedValue({ kind });
    render(<CalendarCallback code="c" state="s" error={null} />);

    expect(await screen.findByText(pattern)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Try again" })).toHaveAttribute("href", SETTINGS);
    expect(completeMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).not.toHaveBeenCalledWith(SETTINGS);
  });

  it("shows the reason when the API is unreachable", async () => {
    completeMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    render(<CalendarCallback code="c" state="s" error={null} />);
    expect(await screen.findByText("Could not reach the API.")).toBeInTheDocument();
  });

  it.each([
    ["getToken rejects", () => getTokenMock.mockRejectedValue(new Error("clerk down"))],
    ["the API call throws", () => completeMock.mockRejectedValue(new Error("boom"))],
  ])("leaves the skeleton and shows Try again when %s, without retrying", async (_name, arrange) => {
    arrange();
    render(<CalendarCallback code="c" state="s" error={null} />);

    expect(await screen.findByText(/Something went wrong/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Try again" })).toHaveAttribute("href", SETTINGS);
    expect(screen.queryByRole("heading", { name: "Connecting your calendar" })).not.toBeInTheDocument();
    expect(completeMock.mock.calls.length).toBeLessThanOrEqual(1);
  });

  it("sends a signed-out owner to sign in without calling the API", async () => {
    getTokenMock.mockResolvedValue(null);
    render(<CalendarCallback code="c" state="s" error={null} />);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/sign-in"));
    expect(completeMock).not.toHaveBeenCalled();
  });
});
