import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken: getTokenMock }) }));

const listMessages = vi.fn();
vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  listInteractionMessages: (...args: unknown[]) => listMessages(...args),
}));

import type {
  InteractionDetail,
  InteractionMessagesResult,
  MessageRead,
} from "@/lib/api/client";
import { InteractionDetailView } from "./detail-view";

const detail = (over: Partial<InteractionDetail> = {}): InteractionDetail => ({
  id: "id-1",
  channel: "test_chat",
  started_at: "2026-10-21T18:30:00Z",
  ended_at: "2026-10-21T18:34:00Z",
  caller: null,
  intent: "BOOK_APPOINTMENT",
  summary: "The customer asked to book a haircut. The appointment was booked for Fri 30 Oct at 3:30 PM.",
  summary_source: "ai",
  status: "completed",
  outcome: "booked",
  requires_human: false,
  timezone: "America/New_York",
  message_count: 3,
  operations: [
    { id: "o1", tool_name: "check_availability", status: "succeeded", label: "3 slots", created_at: "2026-10-21T18:31:00Z", duration_ms: 90 },
    { id: "o2", tool_name: "create_booking", status: "succeeded", label: "booking 1a2b3c4d", created_at: "2026-10-21T18:33:00Z", duration_ms: 120 },
  ],
  ...over,
});

const msg = (n: number, over: Partial<MessageRead> = {}): MessageRead => ({
  sequence_number: n,
  speaker: n % 2 === 1 ? "agent" : "customer",
  text: `message ${n}`,
  created_at: "2026-10-21T18:30:00Z",
  fallback: false,
  ...over,
});

const page = (items: MessageRead[], next_after: number | null = null): InteractionMessagesResult => ({
  kind: "ok",
  data: { items, next_after },
});

const show = (
  d: Parameters<typeof InteractionDetailView>[0]["detail"],
  messages: InteractionMessagesResult = page([msg(1), msg(2), msg(3)]),
) => render(<InteractionDetailView detail={d} messages={messages} backHref="/dashboard/inbox?type=failed" />);

beforeEach(() => {
  getTokenMock.mockReset().mockResolvedValue("tok");
  listMessages.mockReset();
});

describe("InteractionDetailView", () => {
  it("shows the AI summary label, badges, timezone-correct time and the back link with the query", () => {
    show({ kind: "ok", data: detail() });
    expect(screen.getByRole("heading", { level: 1, name: "Test chat" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "AI summary" })).toBeInTheDocument();
    expect(screen.getByText(/The customer asked to book a haircut/)).toBeInTheDocument();
    expect(screen.getByText("Booked")).toBeInTheDocument();
    expect(screen.getByText(/Oct 21, 2026, 2:30 PM EDT/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to inbox" })).toHaveAttribute(
      "href",
      "/dashboard/inbox?type=failed",
    );
  });

  it("labels a template summary as automatic and a missing one as pending", () => {
    const { unmount } = show({ kind: "ok", data: detail({ summary_source: "template" }) });
    expect(screen.getByRole("heading", { name: "Automatic summary" })).toBeInTheDocument();
    unmount();
    show({ kind: "ok", data: detail({ summary: null, summary_source: null }) });
    expect(screen.getByText("Summary pending")).toBeInTheDocument();
  });

  it("renders verified actions with accessible status and the backend label", () => {
    show({
      kind: "ok",
      data: detail({
        operations: [
          ...detail().operations,
          { id: "o3", tool_name: "create_booking", status: "rejected", label: "slot_taken", created_at: "2026-10-21T18:34:00Z", duration_ms: null },
        ],
      }),
    });
    const actions = within(screen.getByRole("list", { name: "Actions" })).getAllByRole("listitem");
    expect(actions).toHaveLength(3);
    expect(actions[0]).toHaveTextContent("check_availability");
    expect(actions[0]).toHaveTextContent("succeeded");
    expect(actions[1]).toHaveTextContent("create_booking");
    expect(actions[1]).toHaveTextContent("booking 1a2b3c4d");
    expect(actions[2]).toHaveTextContent("rejected");
    expect(actions[2]).toHaveTextContent("slot_taken");
  });

  it("says so when there were no actions", () => {
    show({ kind: "ok", data: detail({ operations: [] }) });
    expect(screen.getByText(/took no actions/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Actions" })).not.toBeInTheDocument();
  });

  it("marks failed and escalated conversations", () => {
    show({ kind: "ok", data: detail({ status: "failed", outcome: "failed", requires_human: true }) });
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Needs a person")).toBeInTheDocument();
  });

  it("marks an in-progress conversation", () => {
    show({ kind: "ok", data: detail({ status: "in_progress", outcome: null }) });
    expect(screen.getByText("In progress")).toBeInTheDocument();
  });

  it("labels speakers and marks fallback replies", () => {
    show(
      { kind: "ok", data: detail() },
      page([msg(1, { fallback: true, text: "Sorry about that." }), msg(2)]),
    );
    const messages = within(screen.getByRole("list", { name: "Messages" })).getAllByRole("listitem");
    expect(messages[0]).toHaveTextContent("Agent");
    expect(messages[0]).toHaveTextContent("Fallback reply");
    expect(messages[1]).toHaveTextContent("Customer");
    expect(messages[1]).not.toHaveTextContent("Fallback reply");
  });

  it("shows an empty transcript", () => {
    show({ kind: "ok", data: detail({ message_count: 0 }) }, page([]));
    expect(screen.getByText(/No messages were stored/)).toBeInTheDocument();
  });

  it("shows not found with a way back", () => {
    show({ kind: "not_found" });
    expect(screen.getByRole("heading", { level: 1, name: "Conversation not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to inbox" })).toBeInTheDocument();
  });

  it("shows an error when the detail is unreachable", () => {
    show({ kind: "unreachable", reason: "Could not reach the API." });
    expect(screen.getByRole("heading", { name: "Can't load this conversation" })).toBeInTheDocument();
  });

  it("covers unauthorized and subscription_required", () => {
    const { unmount } = show({ kind: "unauthorized" });
    expect(screen.getByRole("link", { name: "Sign in again" })).toBeInTheDocument();
    unmount();
    show({ kind: "subscription_required" });
    expect(screen.getByRole("link", { name: "Choose plan" })).toBeInTheDocument();
  });
});

describe("Transcript load more", () => {
  const first = page([msg(1), msg(2)], 2);

  it("appends the next page using next_after and then stops offering more", async () => {
    const user = userEvent.setup();
    listMessages.mockResolvedValue(page([msg(3), msg(4)], null));
    show({ kind: "ok", data: detail({ message_count: 4 }) }, first);

    expect(screen.getByText("2 of 4 messages")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));

    await waitFor(() => expect(screen.getByText("message 4")).toBeInTheDocument());
    expect(listMessages).toHaveBeenCalledWith("tok", "id-1", 2);
    expect(screen.getByText("message 1")).toBeInTheDocument();
    expect(screen.getByText("4 of 4 messages")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("does not duplicate messages the server repeats", async () => {
    const user = userEvent.setup();
    listMessages.mockResolvedValue(page([msg(2), msg(3)], null));
    show({ kind: "ok", data: detail({ message_count: 3 }) }, first);
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getByText("message 3")).toBeInTheDocument());
    expect(screen.getAllByText("message 2")).toHaveLength(1);
  });

  it("keeps what is shown and offers Try again after a failure", async () => {
    const user = userEvent.setup();
    listMessages
      .mockResolvedValueOnce({ kind: "unreachable", reason: "Could not reach the API." })
      .mockResolvedValueOnce(page([msg(3)], null));
    show({ kind: "ok", data: detail({ message_count: 3 }) }, first);

    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Can't load the transcript")).toBeInTheDocument();
    expect(screen.getByText("message 1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("message 3")).toBeInTheDocument());
    expect(screen.queryByText("Can't load the transcript")).not.toBeInTheDocument();
  });

  it("shows the failure when the first page could not load, and retries from the start", async () => {
    const user = userEvent.setup();
    listMessages.mockResolvedValue(page([msg(1)], null));
    show({ kind: "ok", data: detail({ message_count: 1 }) }, { kind: "unreachable", reason: "Could not reach the API." });
    expect(screen.getByText("Can't load the transcript")).toBeInTheDocument();
    expect(screen.queryByText(/No messages were stored/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("message 1")).toBeInTheDocument());
    expect(listMessages).toHaveBeenCalledWith("tok", "id-1", 0);
  });

  it("explains an expired session without calling the API", async () => {
    const user = userEvent.setup();
    getTokenMock.mockResolvedValue(null);
    show({ kind: "ok", data: detail({ message_count: 3 }) }, first);
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText(/session has expired/)).toBeInTheDocument();
    expect(listMessages).not.toHaveBeenCalled();
  });
});
