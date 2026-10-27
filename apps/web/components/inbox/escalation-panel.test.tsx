import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: getTokenMock }) }));

const resolveMock = vi.fn();
vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  resolveEscalation: (...args: unknown[]) => resolveMock(...args),
}));

import type { EscalationRead } from "@/lib/api/client";
import { EscalationHeaderBadge, EscalationPanel, EscalationProvider } from "./escalation-panel";

const esc = (over: Partial<EscalationRead> = {}): EscalationRead => ({
  id: "e1",
  interaction_id: "i1",
  reason: "complaint",
  urgency: "normal",
  status: "open",
  sms_status: "sent",
  sms_error: null,
  created_at: "2026-10-21T18:30:00Z",
  resolved_at: null,
  ...over,
});

const show = (items: EscalationRead[], requiresHuman = true) =>
  render(
    <EscalationProvider initial={items}>
      <EscalationHeaderBadge requiresHuman={requiresHuman} />
      <EscalationPanel timezone="America/New_York" />
    </EscalationProvider>,
  );

beforeEach(() => {
  getTokenMock.mockReset().mockResolvedValue("tok");
  resolveMock.mockReset();
});

describe("EscalationPanel", () => {
  it("renders nothing and keeps the legacy badge when there are no escalations", () => {
    show([]);
    expect(screen.queryByRole("heading", { name: "Escalation" })).not.toBeInTheDocument();
    expect(screen.getByText("Needs a person")).toBeInTheDocument();
  });

  it("lists reason, SMS status, time in the business timezone and the header badge", () => {
    show([esc()]);
    expect(screen.getByRole("heading", { name: "Escalation" })).toBeInTheDocument();
    expect(screen.getAllByText("Complaint")).toHaveLength(2); // header badge + row
    expect(screen.getByText(/Needs attention/)).toBeInTheDocument();
    expect(screen.getByText("Sent")).toBeInTheDocument();
    expect(screen.getByText("Oct 21, 2:30 PM")).toBeInTheDocument();
    expect(screen.queryByText("Urgent")).not.toBeInTheDocument();
    expect(screen.queryByText("Needs a person")).not.toBeInTheDocument();
  });

  it("marks urgent ones and shows the SMS error code plainly", () => {
    show([esc({ reason: "urgent", urgency: "high", sms_status: "failed", sms_error: "30007" })]);
    expect(screen.getByText("Urgent", { selector: "[data-slot=badge]" })).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Error code: 30007")).toBeInTheDocument();
  });

  it("does not crash on an unknown reason or SMS status", () => {
    show([esc({ reason: "new_thing", sms_status: "weird" })]);
    expect(screen.getAllByText("New thing").length).toBeGreaterThan(0);
    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });

  it("resolves only after the API confirms, and the header badge follows", async () => {
    let finish!: (value: unknown) => void;
    resolveMock.mockReturnValue(new Promise((r) => (finish = r)));
    const user = userEvent.setup();
    show([esc()]);

    await user.click(screen.getByRole("button", { name: /Resolve/ }));
    expect(resolveMock).toHaveBeenCalledWith("tok", "e1");
    expect(screen.getByRole("button", { name: /Resolving/ })).toBeDisabled();
    expect(screen.getByText(/Needs attention/)).toBeInTheDocument();

    finish({ kind: "ok", data: esc({ status: "resolved", resolved_at: "2026-10-21T19:00:00Z" }) });
    expect(await screen.findByText("Resolved", { selector: "p" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Resolve/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Needs attention/)).not.toBeInTheDocument();
    expect(screen.queryByText("Needs a person")).not.toBeInTheDocument();
  });

  it("shows an error callout and keeps it open when resolving fails", async () => {
    resolveMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    show([esc()]);

    await user.click(screen.getByRole("button", { name: /Resolve/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the API.");
    expect(screen.getByRole("button", { name: /Resolve/ })).toBeEnabled();
    expect(screen.getByText(/Needs attention/)).toBeInTheDocument();
  });

  it("reports a session that has expired without calling the API", async () => {
    getTokenMock.mockResolvedValue(null);
    const user = userEvent.setup();
    show([esc()]);

    await user.click(screen.getByRole("button", { name: /Resolve/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your session has expired");
    expect(resolveMock).not.toHaveBeenCalled();
  });
});
