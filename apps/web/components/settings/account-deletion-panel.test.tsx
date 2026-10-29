import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { AccountDeletionResult } from "@/lib/api/client";

const getTokenMock = vi.fn();
const signOutMock = vi.fn();
const deleteMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
  useClerk: () => ({ signOut: signOutMock }),
}));

vi.mock("@/lib/api/client", () => ({
  deleteAccount: (...args: unknown[]) => deleteMock(...args),
}));

import { AccountDeletionPanel } from "./account-deletion-panel";

const RESULT: AccountDeletionResult = {
  calendar_events_cancelled: 2,
  calendar_events_not_cancelled: 0,
  calendar_token_revoked: true,
  sign_in_deleted: true,
};

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  getTokenMock.mockReset().mockResolvedValue("token-123");
  signOutMock.mockReset().mockResolvedValue(undefined);
  deleteMock.mockReset();
});

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Delete account" }));
  return screen.findByRole("dialog", { name: "Delete account?" });
}

async function confirmDeletion(user: ReturnType<typeof userEvent.setup>) {
  const dialog = await openDialog(user);
  await user.type(within(dialog).getByLabelText(/Type DELETE MY ACCOUNT/), "DELETE MY ACCOUNT");
  await user.click(confirmButton(dialog));
  return dialog;
}

const confirmButton = (dialog: HTMLElement) => within(dialog).getByRole("button", { name: /^Delet(e|ing)/ });

describe("AccountDeletionPanel: danger zone", () => {
  it("starts with a danger zone, no dialog and no result", () => {
    render(<AccountDeletionPanel />);
    expect(screen.getByRole("heading", { name: "Delete account" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("Account deleted")).not.toBeInTheDocument();
    expect(deleteMock).not.toHaveBeenCalled();
  });

  it("lists what is deleted, in order, and what is kept", async () => {
    const user = userEvent.setup();
    render(<AccountDeletionPanel />);
    const dialog = await openDialog(user);

    const deleted = within(dialog).getByRole("region", { name: "Deleted" });
    const items = within(deleted).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent(/Stripe customer record.*ends now, with no refund/);
    expect(items[1]).toHaveTextContent(/Upcoming appointments Tether added to your Google Calendar are cancelled/);
    expect(items[2]).toHaveTextContent(/access is revoked and the stored token is deleted/);
    expect(items[3]).toHaveTextContent(/Your business and everything in it/);
    expect(items[4]).toHaveTextContent(/Your sign-in/);

    const kept = within(dialog).getByRole("region", { name: "Kept" });
    expect(within(kept).getByText(/Past events on your Google Calendar/)).toBeInTheDocument();
    expect(within(kept).getByText(/Stripe keeps under its own obligations/)).toBeInTheDocument();
    expect(within(kept).getByText(/not tied to any business/)).toBeInTheDocument();
  });
});

describe("AccountDeletionPanel: typed confirmation", () => {
  it("keeps the button disabled until the exact phrase is typed", async () => {
    const user = userEvent.setup();
    render(<AccountDeletionPanel />);
    const dialog = await openDialog(user);
    const input = within(dialog).getByLabelText(/Type DELETE MY ACCOUNT to confirm/);

    expect(confirmButton(dialog)).toBeDisabled();
    await user.type(input, "delete my account");
    expect(confirmButton(dialog)).toBeDisabled();
    await user.clear(input);
    await user.type(input, "DELETE MY");
    expect(confirmButton(dialog)).toBeDisabled();
    await user.type(input, " ACCOUNT");
    expect(confirmButton(dialog)).toBeEnabled();
    expect(deleteMock).not.toHaveBeenCalled();
  });

  it("starts empty every time the dialog opens", async () => {
    const user = userEvent.setup();
    render(<AccountDeletionPanel />);
    let dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/Type DELETE MY ACCOUNT/), "DELETE MY ACCOUNT");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    dialog = await openDialog(user);
    expect(within(dialog).getByLabelText(/Type DELETE MY ACCOUNT/)).toHaveValue("");
    expect(confirmButton(dialog)).toBeDisabled();
  });
});

describe("AccountDeletionPanel: deleting", () => {
  it("claims nothing until the 200, then signs out once and says so", async () => {
    const user = userEvent.setup();
    let resolve!: (value: unknown) => void;
    deleteMock.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<AccountDeletionPanel />);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/Type DELETE MY ACCOUNT/), "DELETE MY ACCOUNT");

    await user.click(confirmButton(dialog));

    // In flight: disabled (no double submit), no result claimed, no sign out.
    await waitFor(() => expect(confirmButton(dialog)).toBeDisabled());
    expect(confirmButton(dialog)).toHaveTextContent("Deleting…");
    expect(screen.queryByText("Account deleted")).not.toBeInTheDocument();
    await user.click(confirmButton(dialog));
    expect(deleteMock).toHaveBeenCalledTimes(1);
    expect(deleteMock).toHaveBeenCalledWith("token-123", "DELETE MY ACCOUNT");
    expect(signOutMock).not.toHaveBeenCalled();

    resolve({ kind: "ok", data: RESULT });

    expect(await screen.findByText("Account deleted")).toBeInTheDocument();
    await waitFor(() => expect(signOutMock).toHaveBeenCalledTimes(1));
    expect(signOutMock).toHaveBeenCalledWith({ redirectUrl: "/" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete account?" })).not.toBeInTheDocument());
  });

  it("holds the page, with a Sign out button, when calendar events could not be cancelled", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "ok", data: { ...RESULT, calendar_events_not_cancelled: 2 } });
    render(<AccountDeletionPanel />);
    await confirmDeletion(user);

    expect(await screen.findByText("Account deleted")).toBeInTheDocument();
    expect(screen.getByText(/2 upcoming events could not be cancelled/)).toBeInTheDocument();
    expect(screen.getByText(/Remove them in Google Calendar yourself/)).toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(signOutMock).toHaveBeenCalledWith({ redirectUrl: "/" });
  });

  it("points to Google's permissions page when the token revoke was not confirmed", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({
      kind: "ok",
      data: { ...RESULT, calendar_events_not_cancelled: 1, calendar_token_revoked: false },
    });
    render(<AccountDeletionPanel />);
    await confirmDeletion(user);

    expect(await screen.findByText(/1 upcoming event could not be cancelled/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "myaccount.google.com/permissions" })).toHaveAttribute(
      "href",
      "https://myaccount.google.com/permissions",
    );
  });

  it("holds the page when only the token revoke was unconfirmed", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "ok", data: { ...RESULT, calendar_token_revoked: false } });
    render(<AccountDeletionPanel />);
    await confirmDeletion(user);

    expect(await screen.findByText(/Google did not confirm/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "myaccount.google.com/permissions" })).toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();
  });

  it("signs out at once when there was no calendar connection", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "ok", data: { ...RESULT, calendar_token_revoked: null } });
    render(<AccountDeletionPanel />);
    await confirmDeletion(user);

    await waitFor(() => expect(signOutMock).toHaveBeenCalledWith({ redirectUrl: "/" }));
    expect(screen.queryByText(/Google did not confirm/)).not.toBeInTheDocument();
  });

  it("stays and offers Try again when the sign-in could not be deleted", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValueOnce({ kind: "ok", data: { ...RESULT, sign_in_deleted: false } });
    render(<AccountDeletionPanel />);
    await confirmDeletion(user);

    expect(await screen.findByText("Data deleted, sign-in not removed")).toBeInTheDocument();
    expect(screen.getByText(/couldn.t remove your sign-in/)).toBeInTheDocument();
    expect(screen.queryByText("Account deleted")).not.toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();

    deleteMock.mockResolvedValueOnce({ kind: "ok", data: { ...RESULT, calendar_events_cancelled: 0 } });
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(deleteMock).toHaveBeenCalledTimes(2);
    expect(deleteMock).toHaveBeenLastCalledWith("token-123", "DELETE MY ACCOUNT");
    expect(await screen.findByText("Account deleted")).toBeInTheDocument();
    await waitFor(() => expect(signOutMock).toHaveBeenCalledTimes(1));
  });

  it("keeps the Try again state, with the error, when the retry fails", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValueOnce({ kind: "ok", data: { ...RESULT, sign_in_deleted: false } });
    render(<AccountDeletionPanel />);
    await confirmDeletion(user);
    await screen.findByText("Data deleted, sign-in not removed");

    deleteMock.mockResolvedValueOnce({ kind: "unreachable", reason: "Could not reach the API." });
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText(/Couldn't confirm whether your account was deleted/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(signOutMock).not.toHaveBeenCalled();
  });

  it("says nothing was deleted on a confirmation mismatch", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "confirmation_mismatch" });
    render(<AccountDeletionPanel />);
    const dialog = await confirmDeletion(user);

    expect(await within(dialog).findByText(/That phrase didn't match, so nothing was deleted/)).toBeInTheDocument();
    expect(screen.queryByText("Account deleted")).not.toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();
    await waitFor(() => expect(confirmButton(dialog)).toBeEnabled());
  });

  it("says nothing was deleted when Stripe is unreachable, and allows another try", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "billing_unreachable" });
    render(<AccountDeletionPanel />);
    const dialog = await confirmDeletion(user);

    expect(
      await within(dialog).findByText(
        "We couldn't end your subscription with Stripe, so nothing was deleted. Try again in a few minutes.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Account deleted")).not.toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();
    await waitFor(() => expect(confirmButton(dialog)).toBeEnabled());
  });

  it("does not claim success when the outcome is unknown", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    render(<AccountDeletionPanel />);
    const dialog = await confirmDeletion(user);

    expect(await within(dialog).findByText(/Couldn't confirm whether your account was deleted/)).toBeInTheDocument();
    expect(screen.queryByText("Account deleted")).not.toBeInTheDocument();
    expect(signOutMock).not.toHaveBeenCalled();
  });

  it("asks the owner to sign in again when there is no session token", async () => {
    const user = userEvent.setup();
    getTokenMock.mockResolvedValue(null);
    render(<AccountDeletionPanel />);
    const dialog = await confirmDeletion(user);

    expect(await within(dialog).findByText("Your session has expired. Please sign in again.")).toBeInTheDocument();
    expect(deleteMock).not.toHaveBeenCalled();
  });
});

describe("AccountDeletionPanel: disclosure variant", () => {
  it("is a quiet disclosure that opens the same confirmation dialog", async () => {
    const user = userEvent.setup();
    deleteMock.mockResolvedValue({ kind: "ok", data: RESULT });
    render(<AccountDeletionPanel variant="disclosure" />);

    expect(screen.queryByRole("heading", { name: "Delete account" })).not.toBeInTheDocument();
    await user.click(screen.getByText("Delete account", { selector: "summary" }));
    const dialog = await openDialog(user);

    await user.type(within(dialog).getByLabelText(/Type DELETE MY ACCOUNT/), "DELETE MY ACCOUNT");
    await user.click(confirmButton(dialog));
    expect(await screen.findByText("Account deleted")).toBeInTheDocument();
    await waitFor(() => expect(signOutMock).toHaveBeenCalledTimes(1));
  });
});
