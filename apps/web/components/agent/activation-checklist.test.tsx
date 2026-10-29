import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { AgentReadiness, AgentReadinessResult } from "@/lib/api/client";
import { readiness } from "@/components/dashboard/test-fixtures";

const getTokenMock = vi.fn();
const activateMock = vi.fn();
const deactivateMock = vi.fn();
const readinessMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  activateAgent: (...args: unknown[]) => activateMock(...args),
  deactivateAgent: (...args: unknown[]) => deactivateMock(...args),
  getAgentReadiness: (...args: unknown[]) => readinessMock(...args),
}));

import { ActivationChecklist } from "./activation-checklist";

const ok = (data: AgentReadiness): AgentReadinessResult => ({ kind: "ok", data });

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  getTokenMock.mockReset().mockResolvedValue("token-123");
  activateMock.mockReset();
  deactivateMock.mockReset();
  readinessMock.mockReset();
});

const activateButton = () => screen.getByRole("button", { name: "Activate" });
const status = () => screen.getByRole("heading", { name: "Activation" }).parentElement!;

describe("ActivationChecklist: states", () => {
  it("lists every item with its message and a fix link only on open steps", () => {
    render(<ActivationChecklist result={ok(readiness(["services", "test_chat"]))} />);
    const list = screen.getByRole("list", { name: "Activation checklist" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(7);
    expect(within(list).getByText("Add at least one active service.")).toBeInTheDocument();
    expect(within(list).getByText("Name and greeting are set.")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Fix Services" })).toHaveAttribute(
      "href",
      "/dashboard/agent#services",
    );
    expect(within(list).getByRole("link", { name: "Fix Test conversation" })).toHaveAttribute(
      "href",
      "/dashboard/agent#test-chat",
    );
    expect(within(list).getAllByRole("link")).toHaveLength(2);
  });

  it("shows Inactive and blocks Activate while a required step is open", () => {
    render(<ActivationChecklist result={ok(readiness(["calendar"]))} />);
    expect(within(status()).getByText("Inactive")).toBeInTheDocument();
    expect(activateButton()).toBeDisabled();
    expect(screen.getByText(/Finish the steps marked above/)).toBeInTheDocument();
  });

  it("enables Activate when the API says ready", () => {
    render(<ActivationChecklist result={ok(readiness([]))} />);
    expect(activateButton()).toBeEnabled();
  });

  it("shows Active with Deactivate instead of Activate", () => {
    render(<ActivationChecklist result={ok(readiness([], { active: true }))} />);
    expect(within(status()).getByText("Active")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deactivate" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });

  it("warns when an active receptionist has lost a required step", () => {
    render(<ActivationChecklist result={ok(readiness(["phone"], { active: true }))} />);
    expect(screen.getByText(/1 setup step is no longer complete/)).toBeInTheDocument();
  });

  it("fails alone with a Callout when the checklist could not load", () => {
    render(<ActivationChecklist result={{ kind: "unreachable", reason: "Could not reach the API." }} />);
    expect(screen.getByRole("heading", { name: "Can't load activation status" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });

  it("points to the plan page when the plan is required", () => {
    render(<ActivationChecklist result={{ kind: "subscription_required" }} />);
    expect(screen.getByRole("link", { name: "Choose plan" })).toHaveAttribute("href", "/billing/plan");
  });
});

describe("ActivationChecklist: activate", () => {
  it("shows Active only after the API confirms", async () => {
    const user = userEvent.setup();
    let resolve!: (value: unknown) => void;
    activateMock.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<ActivationChecklist result={ok(readiness([]))} />);

    await user.click(activateButton());
    // Request in flight: still inactive, and the button cannot be pressed again.
    expect(await screen.findByRole("button", { name: "Activating…" })).toBeDisabled();
    expect(within(status()).getByText("Inactive")).toBeInTheDocument();
    expect(within(status()).queryByText("Active")).not.toBeInTheDocument();

    resolve({ kind: "ok", data: readiness([], { active: true }) });
    expect(await screen.findByRole("button", { name: "Deactivate" })).toBeInTheDocument();
    expect(within(status()).getByText("Active")).toBeInTheDocument();
    expect(activateMock).toHaveBeenCalledTimes(1);
    expect(activateMock).toHaveBeenCalledWith("token-123");
  });

  it("stays inactive and re-fetches the checklist after a 409 agent_not_ready", async () => {
    const user = userEvent.setup();
    activateMock.mockResolvedValue({ kind: "not_ready", message: "Finish these first: services." });
    readinessMock.mockResolvedValue({ kind: "ok", data: readiness(["services"]) });
    // The page loaded when the checklist looked ready; the API disagrees.
    render(<ActivationChecklist result={ok(readiness([]))} />);

    await user.click(activateButton());

    expect(await screen.findByText("Finish these first: services.")).toBeInTheDocument();
    await waitFor(() => expect(readinessMock).toHaveBeenCalledWith("token-123"));
    expect(await screen.findByRole("link", { name: "Fix Services" })).toBeInTheDocument();
    expect(within(status()).getByText("Inactive")).toBeInTheDocument();
    expect(activateButton()).toBeDisabled();
  });

  it("says so when the re-fetch after a 409 also fails, and keeps the old items", async () => {
    const user = userEvent.setup();
    activateMock.mockResolvedValue({ kind: "not_ready", message: "Finish these first: services." });
    readinessMock.mockResolvedValue({ kind: "unreachable", reason: "down" });
    render(<ActivationChecklist result={ok(readiness([]))} />);

    await user.click(activateButton());

    expect(await screen.findByText(/Reload the page to see which steps are open/)).toBeInTheDocument();
    expect(within(status()).getByText("Inactive")).toBeInTheDocument();
  });

  it("does not claim activation when the outcome is unknown", async () => {
    const user = userEvent.setup();
    activateMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    render(<ActivationChecklist result={ok(readiness([]))} />);

    await user.click(activateButton());

    expect(await screen.findByText(/Couldn't confirm activation/)).toBeInTheDocument();
    expect(within(status()).getByText("Inactive")).toBeInTheDocument();
    expect(activateButton()).toBeEnabled();
  });

  it("asks the owner to sign in again when there is no session token", async () => {
    const user = userEvent.setup();
    getTokenMock.mockResolvedValue(null);
    render(<ActivationChecklist result={ok(readiness([]))} />);

    await user.click(activateButton());

    expect(await screen.findByText("Your session has expired. Please sign in again.")).toBeInTheDocument();
    expect(activateMock).not.toHaveBeenCalled();
  });
});

describe("ActivationChecklist: deactivate", () => {
  it("goes through a confirm dialog that says callers hear you're not available", async () => {
    const user = userEvent.setup();
    deactivateMock.mockResolvedValue({ kind: "ok", data: readiness([], { active: false }) });
    render(<ActivationChecklist result={ok(readiness([], { active: true }))} />);

    await user.click(screen.getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog", { name: "Deactivate your receptionist?" });
    expect(within(dialog).getByText(/Callers will hear that you're not available/)).toBeInTheDocument();
    expect(deactivateMock).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    await waitFor(() => expect(within(status()).getByText("Inactive")).toBeInTheDocument());
    expect(deactivateMock).toHaveBeenCalledWith("token-123");
    expect(screen.getByRole("button", { name: "Activate" })).toBeEnabled();
  });

  it("keeps Active and shows the error in the dialog when deactivation is not confirmed", async () => {
    const user = userEvent.setup();
    deactivateMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    render(<ActivationChecklist result={ok(readiness([], { active: true }))} />);

    await user.click(screen.getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog", { name: "Deactivate your receptionist?" });
    await user.click(within(dialog).getByRole("button", { name: "Deactivate" }));

    expect(await within(dialog).findByText(/Couldn't confirm deactivation/)).toBeInTheDocument();
    expect(within(status()).getByText("Active")).toBeInTheDocument();
  });

  it("does nothing when the dialog is cancelled", async () => {
    const user = userEvent.setup();
    render(<ActivationChecklist result={ok(readiness([], { active: true }))} />);

    await user.click(screen.getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog", { name: "Deactivate your receptionist?" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(deactivateMock).not.toHaveBeenCalled();
    expect(within(status()).getByText("Active")).toBeInTheDocument();
  });
});

describe("ActivationChecklist: check again", () => {
  it("replaces the items with the API's current ones", async () => {
    const user = userEvent.setup();
    readinessMock.mockResolvedValue({ kind: "ok", data: readiness([]) });
    render(<ActivationChecklist result={ok(readiness(["services"]))} />);
    expect(activateButton()).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Check again" }));

    await waitFor(() => expect(activateButton()).toBeEnabled());
    expect(screen.queryByRole("link", { name: "Fix Services" })).not.toBeInTheDocument();
  });

  it("keeps the old items and says so when the refresh fails", async () => {
    const user = userEvent.setup();
    readinessMock.mockResolvedValue({ kind: "unreachable", reason: "down" });
    render(<ActivationChecklist result={ok(readiness(["services"]))} />);

    await user.click(screen.getByRole("button", { name: "Check again" }));

    expect(await screen.findByText(/Couldn't refresh the checklist/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fix Services" })).toBeInTheDocument();
  });
});
