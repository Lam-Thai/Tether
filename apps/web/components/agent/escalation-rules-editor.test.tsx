import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
const putMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  updateEscalationSettings: (...args: unknown[]) => putMock(...args),
}));

import type { EscalationSettingsRead, EscalationSettingsResult } from "@/lib/api/client";
import { EscalationRulesEditor } from "./escalation-rules-editor";

const DEFAULTS: EscalationSettingsRead = {
  contact_phone: null,
  sms_enabled: false,
  clarification_threshold: 2,
  urgent_keywords: [],
  customized: false,
  sms_configured: true,
};

const ok = (data: EscalationSettingsRead): EscalationSettingsResult => ({ kind: "ok", data });
const SAVE = "Save escalation rules";

describe("EscalationRulesEditor", () => {
  beforeEach(() => {
    getTokenMock.mockReset().mockResolvedValue("token-123");
    putMock.mockReset();
  });

  it("shows defaults, labelled as defaults, with the trial-account note", () => {
    render(<EscalationRulesEditor result={ok(DEFAULTS)} />);

    expect(screen.getByRole("heading", { name: "Escalation rules" })).toBeInTheDocument();
    expect(screen.getByText("Using defaults")).toBeInTheDocument();
    expect(screen.queryByText("Demo mode")).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Text me when a customer needs a person" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByLabelText("Contact phone")).toHaveValue("");
    expect(screen.getByLabelText("Clarification attempts")).toHaveValue("2");
    expect(screen.getByText(/can only text numbers you have verified/)).toBeInTheDocument();
    expect(screen.getByText("No urgent keywords yet.")).toBeInTheDocument();
  });

  it("explains demo mode when SMS is not configured", () => {
    render(<EscalationRulesEditor result={ok({ ...DEFAULTS, sms_configured: false })} />);
    expect(screen.getByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByText(/nothing is texted/)).toBeInTheDocument();
  });

  it("requires a phone when text alerts are on and does not call the API", async () => {
    const user = userEvent.setup();
    render(<EscalationRulesEditor result={ok({ ...DEFAULTS, customized: true })} />);

    await user.click(screen.getByRole("switch"));
    await user.click(screen.getByRole("button", { name: SAVE }));

    expect(await screen.findByText(/Add a phone number to receive text alerts/)).toBeInTheDocument();
    expect(putMock).not.toHaveBeenCalled();
  });

  it("rejects a phone that is not E.164", async () => {
    const user = userEvent.setup();
    render(<EscalationRulesEditor result={ok(DEFAULTS)} />);

    await user.type(screen.getByLabelText("Contact phone"), "415 555 0123");
    await user.click(screen.getByRole("button", { name: SAVE }));

    expect(await screen.findByText("Use international format, such as +14155550123.")).toBeInTheDocument();
    expect(putMock).not.toHaveBeenCalled();
  });

  it("adds and removes keywords, rejecting blanks and case-insensitive duplicates", async () => {
    const user = userEvent.setup();
    render(<EscalationRulesEditor result={ok(DEFAULTS)} />);

    await user.click(screen.getByRole("button", { name: "Add keyword" }));
    expect(screen.getByText("Type a keyword first.")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Urgent keywords"), "  Gas leak {enter}");
    expect(screen.getByRole("list", { name: "Added keywords" })).toHaveTextContent("Gas leak");
    expect(putMock).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("Urgent keywords"), "gas LEAK");
    await user.click(screen.getByRole("button", { name: "Add keyword" }));
    expect(screen.getByText("That keyword is already in the list.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove Gas leak" }));
    expect(screen.getByText("No urgent keywords yet.")).toBeInTheDocument();
  });

  it("sends the four fields and shows Saved only after the API confirms", async () => {
    let resolve!: (value: unknown) => void;
    putMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    render(<EscalationRulesEditor result={ok(DEFAULTS)} />);

    await user.click(screen.getByRole("switch"));
    await user.type(screen.getByLabelText("Contact phone"), "+14155550123");
    await user.selectOptions(screen.getByLabelText("Clarification attempts"), "3");
    await user.type(screen.getByLabelText("Urgent keywords"), "flood{enter}");
    await user.click(screen.getByRole("button", { name: SAVE }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0]![0]).toBe("token-123");
    expect(putMock.mock.calls[0]![1]).toEqual({
      contact_phone: "+14155550123",
      sms_enabled: true,
      clarification_threshold: 3,
      urgent_keywords: ["flood"],
    });
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    // Locked while the save is in flight, so no edit is silently replaced by the response.
    expect(screen.getByLabelText("Contact phone")).toBeDisabled();
    expect(screen.getByLabelText("Urgent keywords")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add keyword" })).toBeDisabled();

    resolve({
      kind: "ok",
      data: {
        contact_phone: "+14155550123",
        sms_enabled: true,
        clarification_threshold: 3,
        urgent_keywords: ["flood"],
        customized: true,
        sms_configured: true,
      },
    });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.queryByText("Using defaults")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Contact phone")).toBeEnabled();
  });

  it("sends a blank phone as null", async () => {
    putMock.mockResolvedValue({ kind: "ok", data: { ...DEFAULTS, customized: true } });
    const user = userEvent.setup();
    render(<EscalationRulesEditor result={ok(DEFAULTS)} />);

    await user.click(screen.getByRole("button", { name: SAVE }));
    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0]![1]).toMatchObject({ contact_phone: null, sms_enabled: false });
  });

  it("shows the API's contact_phone_required message and never Saved", async () => {
    putMock.mockResolvedValue({ kind: "contact_phone_required", message: "A contact phone is required." });
    const user = userEvent.setup();
    render(<EscalationRulesEditor result={ok(DEFAULTS)} />);

    await user.click(screen.getByRole("button", { name: SAVE }));
    expect(await screen.findByText("A contact phone is required.")).toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("shows a signed-out message on 401 and a plan message on 402", async () => {
    putMock.mockResolvedValueOnce({ kind: "unauthorized" });
    const user = userEvent.setup();
    render(<EscalationRulesEditor result={ok(DEFAULTS)} />);

    await user.click(screen.getByRole("button", { name: SAVE }));
    expect(await screen.findByText(/Your session has expired/)).toBeInTheDocument();

    putMock.mockResolvedValueOnce({ kind: "subscription_required" });
    await user.click(screen.getByRole("button", { name: SAVE }));
    expect(await screen.findByRole("link", { name: "Choose a plan" })).toBeInTheDocument();
  });

  it("shows the unreachable reason when the save fails", async () => {
    putMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    render(<EscalationRulesEditor result={ok(DEFAULTS)} />);

    await user.click(screen.getByRole("button", { name: SAVE }));
    expect(await screen.findByText("Could not reach the API.")).toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("renders a section-only failure when the load failed", () => {
    render(<EscalationRulesEditor result={{ kind: "unreachable", reason: "Could not reach the API." }} />);
    expect(screen.getByText("Can't load your escalation rules")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: SAVE })).not.toBeInTheDocument();
  });
});
