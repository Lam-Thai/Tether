import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { TestDataReport } from "@/lib/api/client";

const getTokenMock = vi.fn();
const deleteMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  deleteTestData: (...args: unknown[]) => deleteMock(...args),
}));

import { TestDataPanel } from "./test-data-panel";

const REPORT: TestDataReport = {
  interactions: 12,
  messages: 80,
  bookings: 3,
  escalations: 2,
  usage_events: 40,
  calendar_events_cancelled: 2,
  calendar_events_not_cancelled: 0,
};

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  getTokenMock.mockReset().mockResolvedValue("token-123");
  deleteMock.mockReset();
});

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Delete test data" }));
  return screen.findByRole("dialog", { name: "Delete test data?" });
}

const confirmButton = (dialog: HTMLElement) => within(dialog).getByRole("button", { name: /^Delet(e|ing)/ });

describe("TestDataPanel: danger zone", () => {
  it("starts with the danger zone and the settings sub-nav, and shows no result", () => {
    render(<TestDataPanel />);
    expect(screen.getByRole("heading", { name: "Data" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Data" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { name: "Delete test data" })).toBeInTheDocument();
    expect(screen.queryByText("Test data deleted")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lists what is deleted and kept, including calendar events and today's limits", async () => {
    const user = userEvent.setup();
    render(<TestDataPanel />);
    const dialog = await openDialog(user);

    const deleted = within(dialog).getByRole("region", { name: "Deleted" });
    expect(within(deleted).getByText(/All conversations and their messages/)).toBeInTheDocument();
    expect(within(deleted).getByText(/Google Calendar events for confirmed bookings are cancelled/)).toBeInTheDocument();
    const kept = within(dialog).getByRole("region", { name: "Kept" });
    expect(within(kept).getByText(/receptionist settings, services, FAQs/)).toBeInTheDocument();
    expect(within(kept).getByText(/daily limits are unchanged/)).toBeInTheDocument();
  });
});

describe("TestDataPanel: typed confirmation", () => {
  it("keeps the button disabled until the exact phrase is typed", async () => {
    const user = userEvent.setup();
    render(<TestDataPanel />);
    const dialog = await openDialog(user);
    const input = within(dialog).getByLabelText(/Type DELETE TEST DATA to confirm/);

    expect(confirmButton(dialog)).toBeDisabled();
    await user.type(input, "delete test data");
    expect(confirmButton(dialog)).toBeDisabled();
    await user.clear(input);
    await user.type(input, "DELETE TEST");
    expect(confirmButton(dialog)).toBeDisabled();
    await user.type(input, " DATA");
    expect(confirmButton(dialog)).toBeEnabled();
    expect(deleteMock).not.toHaveBeenCalled();
  });

  it("starts empty every time the dialog opens", async () => {
    const user = userEvent.setup();
    render(<TestDataPanel />);
    let dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/Type DELETE TEST DATA/), "DELETE TEST DATA");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    dialog = await openDialog(user);
    expect(within(dialog).getByLabelText(/Type DELETE TEST DATA/)).toHaveValue("");
    expect(confirmButton(dialog)).toBeDisabled();
  });
});

describe("TestDataPanel: deleting", () => {
  it("shows nothing as deleted until the 200, then the API's counts", async () => {
    const user = userEvent.setup();
    let resolve!: (value: unknown) => void;
    deleteMock.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<TestDataPanel />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/Type DELETE TEST DATA/), "DELETE TEST DATA");

    await user.click(confirmButton(dialog));

    // In flight: the button is disabled (no double submit) and no result is claimed.
    await waitFor(() => expect(confirmButton(dialog)).toBeDisabled());
    expect(confirmButton(dialog)).toHaveTextContent("Deleting…");
    expect(screen.queryByText("Test data deleted")).not.toBeInTheDocument();
    await user.click(confirmButton(dialog));
    expect(deleteMock).toHaveBeenCalledTimes(1);
    expect(deleteMock).toHaveBeenCalledWith("token-123", "DELETE TEST DATA");

    resolve({ kind: "ok", data: REPORT });

    expect(await screen.findByText("Test data deleted")).toBeInTheDocument();
    const counts = screen.getByLabelText("Deleted counts");
    expect(counts).toHaveTextContent("Conversations12");
    expect(counts).toHaveTextContent("Messages80");
    expect(counts).toHaveTextContent("Bookings3");
    expect(counts).toHaveTextContent("Escalations2");
    expect(counts).toHaveTextContent("Usage events40");
    expect(counts).toHaveTextContent("Calendar events cancelled2");
    expect(screen.queryByText("Some calendar events remain")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete test data?" })).not.toBeInTheDocument());
  });

  it("warns to remove calendar events that could not be cancelled", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "ok", data: { ...REPORT, calendar_events_not_cancelled: 2 } });
    render(<TestDataPanel />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/Type DELETE TEST DATA/), "DELETE TEST DATA");
    await user.click(confirmButton(dialog));

    expect(await screen.findByText("Some calendar events remain")).toBeInTheDocument();
    expect(screen.getByText(/2 calendar events could not be cancelled/)).toBeInTheDocument();
    expect(screen.getByText(/Remove them in Google Calendar/)).toBeInTheDocument();
  });

  it("says nothing was deleted on a confirmation mismatch and shows no counts", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "confirmation_mismatch" });
    render(<TestDataPanel />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/Type DELETE TEST DATA/), "DELETE TEST DATA");
    await user.click(confirmButton(dialog));

    expect(await within(dialog).findByText(/That phrase didn't match, so nothing was deleted/)).toBeInTheDocument();
    expect(screen.queryByText("Test data deleted")).not.toBeInTheDocument();
    await waitFor(() => expect(confirmButton(dialog)).toBeEnabled());
  });

  it("does not claim success when the outcome is unknown", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    render(<TestDataPanel />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/Type DELETE TEST DATA/), "DELETE TEST DATA");
    await user.click(confirmButton(dialog));

    expect(await within(dialog).findByText(/Couldn't confirm that the data was deleted/)).toBeInTheDocument();
    expect(screen.queryByText("Test data deleted")).not.toBeInTheDocument();
  });

  it("asks the owner to sign in again when there is no session token", async () => {
    const user = userEvent.setup();
    getTokenMock.mockResolvedValue(null);
    render(<TestDataPanel />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/Type DELETE TEST DATA/), "DELETE TEST DATA");
    await user.click(confirmButton(dialog));

    expect(await within(dialog).findByText("Your session has expired. Please sign in again.")).toBeInTheDocument();
    expect(deleteMock).not.toHaveBeenCalled();
  });
});
