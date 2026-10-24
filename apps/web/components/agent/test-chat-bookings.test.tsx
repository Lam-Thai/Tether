import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const replaceMock = vi.fn();
const getTokenMock = vi.fn();
const sendMock = vi.fn();
const bookingsMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, refresh: vi.fn(), push: vi.fn() }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  sendTestMessage: (...args: unknown[]) => sendMock(...args),
  endTestChat: vi.fn(),
  getTestTranscript: vi.fn(),
  listChatBookings: (...args: unknown[]) => bookingsMock(...args),
}));

import type { BookingRead, TranscriptRead, TurnRead } from "@/lib/api/client";
import { TestChat } from "./test-chat";

const ID = "22222222-2222-4222-8222-222222222222";

const booking: BookingRead = {
  id: "b1",
  service_name: "Haircut",
  start: "2026-10-09T15:30:00-04:00",
  end: "2026-10-09T16:15:00-04:00",
  date: "2026-10-09",
  label: "15:30",
  customer_name: "Alex",
  status: "confirmed",
};

function turn(overrides: Partial<TurnRead> = {}): TurnRead {
  return {
    interaction_id: ID,
    reply_text: "Booked.",
    intent: "BOOK",
    status: "active",
    ended: false,
    fallback: false,
    usage_limited: false,
    messages: [
      { sequence_number: 1, speaker: "customer", text: "3:30 please", created_at: "2026-01-01T00:00:00Z", fallback: false },
      { sequence_number: 2, speaker: "agent", text: "Booked.", created_at: "2026-01-01T00:00:00Z", fallback: false },
    ],
    ...overrides,
  };
}

const restoredTranscript: TranscriptRead = {
  interaction_id: ID,
  status: "active",
  ended: false,
  started_at: "2026-01-01T00:00:00Z",
  ended_at: null,
  messages: [{ sequence_number: 1, speaker: "agent", text: "Hello", created_at: "2026-01-01T00:00:00Z", fallback: false }],
};

async function sendMessage(user: ReturnType<typeof userEvent.setup>, text = "3:30 please") {
  await user.type(screen.getByLabelText("Message"), text);
  await user.click(screen.getByRole("button", { name: "Send" }));
}

describe("TestChat bookings panel", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    getTokenMock.mockReset().mockResolvedValue("token-123");
    sendMock.mockReset();
    bookingsMock.mockReset();
  });

  it("shows no-chat state and makes no request before a chat exists", () => {
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);
    expect(screen.getByText(/No chat yet/)).toBeInTheDocument();
    expect(bookingsMock).not.toHaveBeenCalled();
  });

  it("refreshes after a successful turn and shows the confirmed booking", async () => {
    sendMock.mockResolvedValue({ kind: "ok", data: turn() });
    bookingsMock.mockResolvedValue({ kind: "ok", data: [booking] });
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await sendMessage(userEvent.setup());

    expect(await screen.findByText("Fri, Oct 9, 3:30 PM")).toBeInTheDocument();
    expect(bookingsMock).toHaveBeenCalledWith("token-123", ID);
    expect(screen.getByText("Confirmed")).toBeInTheDocument();
  });

  it("does not refresh after a failed send", async () => {
    sendMock.mockResolvedValue({ kind: "busy" });
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);
    await sendMessage(userEvent.setup());
    await screen.findByText(/Still working on your last message/);
    expect(bookingsMock).not.toHaveBeenCalled();
  });

  it("loads on restore, showing loading first", async () => {
    let resolve!: (value: unknown) => void;
    bookingsMock.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: restoredTranscript }} />);

    expect(screen.getByText("Loading bookings")).toBeInTheDocument();
    resolve({ kind: "ok", data: [booking] });
    expect(await screen.findByText("Haircut")).toBeInTheDocument();
    expect(bookingsMock).toHaveBeenCalledWith("token-123", ID);
  });

  it("shows a failure and retries", async () => {
    bookingsMock
      .mockResolvedValueOnce({ kind: "unreachable", reason: "Could not reach the API." })
      .mockResolvedValueOnce({ kind: "ok", data: [booking] });
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: restoredTranscript }} />);

    expect(await screen.findByText("Bookings did not load")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Haircut")).toBeInTheDocument();
  });

  it("clears on New chat and ignores a stale response", async () => {
    let resolve!: (value: unknown) => void;
    bookingsMock.mockReturnValue(new Promise((r) => (resolve = r)));
    render(
      <TestChat
        agentName="Riley"
        chat={{ kind: "restored", transcript: { ...restoredTranscript, ended: true } }}
      />,
    );
    expect(screen.getByText("Loading bookings")).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.getByText(/No chat yet/)).toBeInTheDocument();

    resolve({ kind: "ok", data: [booking] });
    await waitFor(() => expect(bookingsMock).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    expect(screen.getByText(/No chat yet/)).toBeInTheDocument();
    expect(screen.queryByText("Haircut")).not.toBeInTheDocument();
  });

  it("keeps only the newest refresh when responses arrive out of order", async () => {
    const pending: Array<(value: unknown) => void> = [];
    bookingsMock.mockImplementation(() => new Promise((r) => pending.push(r)));
    sendMock.mockResolvedValue({ kind: "ok", data: turn() });
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: restoredTranscript }} />);
    await waitFor(() => expect(pending).toHaveLength(1));

    await sendMessage(userEvent.setup());
    await waitFor(() => expect(pending).toHaveLength(2));

    pending[1]!({ kind: "ok", data: [booking] });
    expect(await screen.findByText("Haircut")).toBeInTheDocument();
    pending[0]!({ kind: "ok", data: [] });
    await Promise.resolve();
    expect(screen.getByText("Haircut")).toBeInTheDocument();
  });
});
