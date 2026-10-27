import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { AvailableNumber, PhoneNumber, PhoneNumbers } from "@/lib/api/client";

const getTokenMock = vi.fn();
const associateMock = vi.fn();
const testMock = vi.fn();
const disconnectMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  associatePhoneNumber: (...args: unknown[]) => associateMock(...args),
  testPhoneNumber: (...args: unknown[]) => testMock(...args),
  disconnectPhoneNumber: (...args: unknown[]) => disconnectMock(...args),
}));

import { PhonePanel } from "./phone-panel";

const NUMBER: PhoneNumber = {
  id: "num-1",
  phone_number: "+15551234567",
  provider_number_sid: "PN" + "a".repeat(32),
  status: "connected",
  last_error: null,
  verified_at: "2026-10-27T09:00:00Z",
  created_at: "2026-10-27T08:00:00Z",
};

const AVAILABLE: AvailableNumber = {
  provider_number_sid: "PN" + "b".repeat(32),
  phone_number: "+15559876543",
  friendly_name: null,
};

const data = (overrides: Partial<PhoneNumbers> = {}): PhoneNumbers => ({
  configured: true,
  trial: false,
  numbers: [],
  available: [],
  available_error: null,
  ...overrides,
});

const renderOk = (overrides: Partial<PhoneNumbers> = {}) =>
  render(<PhonePanel result={{ kind: "ok", data: data(overrides) }} />);

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  getTokenMock.mockReset().mockResolvedValue("token-123");
  associateMock.mockReset();
  testMock.mockReset();
  disconnectMock.mockReset();
});

describe("PhonePanel: states", () => {
  it("shows a calm demo mode when telephony is not configured", () => {
    renderOk({ configured: false, trial: null });
    expect(screen.getByText("Phone calls are in demo mode")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Test chat" })).toHaveAttribute("href", "/dashboard/agent");
    expect(screen.queryByText(/trial/i)).not.toBeInTheDocument();
  });

  it("treats a not_configured result as demo mode too", () => {
    render(<PhonePanel result={{ kind: "not_configured" }} />);
    expect(screen.getByText("Phone calls are in demo mode")).toBeInTheDocument();
  });

  it("shows a load failure with a reload hint", () => {
    render(<PhonePanel result={{ kind: "unavailable" }} />);
    expect(screen.getByRole("heading", { name: "Can't load your phone numbers" })).toBeInTheDocument();
    expect(screen.getByText(/Twilio didn't respond/)).toBeInTheDocument();
  });

  it("links to billing when a plan is required", () => {
    render(<PhonePanel result={{ kind: "subscription_required" }} />);
    expect(screen.getByRole("link", { name: "Choose plan" })).toHaveAttribute("href", "/billing/plan");
  });

  it("shows empty states for no numbers and nothing available", () => {
    renderOk();
    expect(screen.getByText("No number connected")).toBeInTheDocument();
    expect(screen.getByText("No unassigned numbers")).toBeInTheDocument();
  });

  it("explains an available_error instead of the empty state", () => {
    renderOk({ available_error: "unavailable" });
    expect(screen.getByText("Can't load your Twilio numbers")).toBeInTheDocument();
    expect(screen.queryByText("No unassigned numbers")).not.toBeInTheDocument();
  });

  it("never offers to buy a number", () => {
    renderOk({ numbers: [NUMBER], available: [AVAILABLE], trial: true });
    expect(screen.queryByText(/buy|purchase/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /buy|purchase/i })).not.toBeInTheDocument();
  });
});

describe("PhonePanel: trial notice", () => {
  it("shows the restrictions and how to test when trial is true", () => {
    renderOk({ trial: true });
    expect(screen.getByText("Twilio trial account")).toBeInTheDocument();
    expect(screen.getByText(/Only verified caller IDs can call the number/)).toBeInTheDocument();
    expect(screen.getByText(/trial message before the agent answers/)).toBeInTheDocument();
    expect(screen.getByText(/turn-based/)).toBeInTheDocument();
    expect(screen.getByText(/call the number from a phone you have verified/)).toBeInTheDocument();
  });

  it("still shows it, hedged, when trial is unknown", () => {
    renderOk({ trial: null });
    expect(screen.getByText("Twilio trial account")).toBeInTheDocument();
    expect(screen.getByText(/couldn't confirm whether/)).toBeInTheDocument();
  });

  it("is hidden for a paid account", () => {
    renderOk({ trial: false });
    expect(screen.queryByText("Twilio trial account")).not.toBeInTheDocument();
  });
});

describe("PhonePanel: number status", () => {
  it("shows the formatted number and the verified status", () => {
    renderOk({ numbers: [NUMBER] });
    expect(screen.getByText("+1 555 123 4567")).toBeInTheDocument();
    expect(screen.getByText("Connected · webhooks verified")).toBeInTheDocument();
  });

  it("does not claim verification without verified_at", () => {
    renderOk({ numbers: [{ ...NUMBER, verified_at: null }] });
    expect(screen.queryByText("Connected · webhooks verified")).not.toBeInTheDocument();
    expect(screen.getByText("Connected · not verified yet")).toBeInTheDocument();
  });

  it.each([
    ["voice_url_mismatch", /Voice URL on Twilio no longer points at Tether/],
    ["status_callback_mismatch", /status callback on Twilio no longer points at Tether/],
    ["number_missing", /no longer on your Twilio account/],
    ["unavailable", /couldn't reach Twilio/],
  ] as const)("shows a warning for misconfigured: %s", (code, pattern) => {
    renderOk({ numbers: [{ ...NUMBER, status: "misconfigured", last_error: code, verified_at: null }] });
    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    expect(screen.getByText(pattern)).toBeInTheDocument();
    expect(screen.queryByText(/webhooks verified/)).not.toBeInTheDocument();
  });

  it("notes an unreachable Twilio on a connected number", () => {
    renderOk({ numbers: [{ ...NUMBER, last_error: "unavailable" }] });
    expect(screen.getByText(/couldn't reach Twilio/)).toBeInTheDocument();
  });
});

describe("PhonePanel: associate", () => {
  it("moves the number into Your numbers only after the API confirms", async () => {
    const user = userEvent.setup();
    let resolve!: (value: unknown) => void;
    associateMock.mockReturnValue(new Promise((r) => (resolve = r)));
    renderOk({ available: [AVAILABLE] });

    await user.click(screen.getByRole("button", { name: "Connect +1 555 987 6543" }));
    expect(associateMock).toHaveBeenCalledWith("token-123", AVAILABLE.provider_number_sid);
    expect(screen.queryByText("Connected · webhooks verified")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect \+1 555 987 6543/ })).toBeDisabled();

    resolve({
      kind: "ok",
      data: { ...NUMBER, phone_number: AVAILABLE.phone_number, provider_number_sid: AVAILABLE.provider_number_sid },
    });
    expect(await screen.findByText("Connected · webhooks verified")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Connect / })).not.toBeInTheDocument();
  });

  it("shows the in-use copy without naming a business and keeps the number available", async () => {
    const user = userEvent.setup();
    associateMock.mockResolvedValue({ kind: "in_use" });
    renderOk({ available: [AVAILABLE] });

    await user.click(screen.getByRole("button", { name: /Connect/ }));
    expect(await screen.findByText("That number is already connected to another business.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Connect/ })).toBeEnabled();
    expect(screen.queryByText("Connected · webhooks verified")).not.toBeInTheDocument();
  });

  it("shows the Twilio-unreachable copy", async () => {
    const user = userEvent.setup();
    associateMock.mockResolvedValue({ kind: "unavailable" });
    renderOk({ available: [AVAILABLE] });

    await user.click(screen.getByRole("button", { name: /Connect/ }));
    expect(await screen.findByText("Twilio didn't respond. Try again in a minute.")).toBeInTheDocument();
  });
});

describe("PhonePanel: test", () => {
  it("shows the refreshed status from the response", async () => {
    const user = userEvent.setup();
    const broken: PhoneNumber = { ...NUMBER, status: "misconfigured", last_error: "voice_url_mismatch", verified_at: null };
    testMock.mockResolvedValue({ kind: "ok", data: NUMBER });
    renderOk({ numbers: [broken] });

    expect(screen.getByText("Needs attention")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(testMock).toHaveBeenCalledWith("token-123", "num-1");
    expect(await screen.findByText("Connected · webhooks verified")).toBeInTheDocument();
    expect(screen.queryByText("Needs attention")).not.toBeInTheDocument();
  });

  it("keeps the old status and shows an error when Twilio can't be reached", async () => {
    const user = userEvent.setup();
    testMock.mockResolvedValue({ kind: "unavailable" });
    renderOk({ numbers: [NUMBER] });

    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText("Twilio didn't respond. Try again in a minute.")).toBeInTheDocument();
    expect(screen.getByText("Connected · webhooks verified")).toBeInTheDocument();
  });
});

describe("PhonePanel: disconnect", () => {
  async function openDialog(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    return screen.findByRole("dialog", { name: "Disconnect number?" });
  }

  it("asks first and makes no call until confirmed", async () => {
    const user = userEvent.setup();
    renderOk({ numbers: [NUMBER] });

    const dialog = await openDialog(user);
    expect(within(dialog).getByText(/will stop reaching your receptionist/)).toBeInTheDocument();
    expect(disconnectMock).not.toHaveBeenCalled();
    expect(screen.getByText("+1 555 123 4567", { selector: "p.font-mono" })).toBeInTheDocument();
  });

  it("removes the number and confirms when the webhooks were cleared", async () => {
    const user = userEvent.setup();
    disconnectMock.mockResolvedValue({ kind: "ok", webhooksCleared: true });
    renderOk({ numbers: [NUMBER] });

    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    expect(disconnectMock).toHaveBeenCalledWith("token-123", "num-1");
    expect(await screen.findByText("Number disconnected")).toBeInTheDocument();
    expect(screen.queryByText("Connected · webhooks verified")).not.toBeInTheDocument();
    expect(screen.getByText("No number connected")).toBeInTheDocument();
  });

  it("warns that the Voice URL is still set when webhooks_cleared is false", async () => {
    const user = userEvent.setup();
    disconnectMock.mockResolvedValue({ kind: "ok", webhooksCleared: false });
    renderOk({ numbers: [NUMBER] });

    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText(/Voice URL is still set on Twilio/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Twilio console" })).toHaveAttribute(
      "href",
      "https://console.twilio.com/",
    );
    expect(screen.queryByText("Number disconnected")).not.toBeInTheDocument();
  });

  it("keeps the number and shows the error when the call fails", async () => {
    const user = userEvent.setup();
    disconnectMock.mockResolvedValue({ kind: "unavailable" });
    renderOk({ numbers: [NUMBER] });

    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    expect(await within(dialog).findByText("Twilio didn't respond. Try again in a minute.")).toBeInTheDocument();
    expect(screen.getByText("Connected · webhooks verified")).toBeInTheDocument();
  });
});
