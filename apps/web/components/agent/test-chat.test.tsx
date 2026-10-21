import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const replaceMock = vi.fn();
const getTokenMock = vi.fn();
const sendMock = vi.fn();
const endMock = vi.fn();
const transcriptMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, refresh: vi.fn(), push: vi.fn() }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  sendTestMessage: (...args: unknown[]) => sendMock(...args),
  endTestChat: (...args: unknown[]) => endMock(...args),
  getTestTranscript: (...args: unknown[]) => transcriptMock(...args),
}));

import type { MessageRead, TranscriptRead, TurnRead } from "@/lib/api/client";
import { CONCURRENCY_NOTE, FALLBACK_NOTE, TestChat, USAGE_LIMITED_NOTE } from "./test-chat";

const ID = "22222222-2222-4222-8222-222222222222";

const msg = (n: number, speaker: "agent" | "customer", text: string): MessageRead => ({
  sequence_number: n,
  speaker,
  text,
  created_at: "2026-01-01T00:00:00Z",
  fallback: false,
});

function turn(overrides: Partial<TurnRead> = {}): TurnRead {
  return {
    interaction_id: ID,
    reply_text: "We open at nine.",
    intent: "BUSINESS_INFO",
    status: "active",
    ended: false,
    fallback: false,
    usage_limited: false,
    messages: [
      msg(1, "agent", "Hi, thanks for contacting Acme!"),
      msg(2, "customer", "When do you open?"),
      msg(3, "agent", "We open at nine."),
    ],
    ...overrides,
  };
}

function transcript(overrides: Partial<TranscriptRead> = {}): TranscriptRead {
  return {
    interaction_id: ID,
    status: "active",
    ended: false,
    started_at: "2026-01-01T00:00:00Z",
    ended_at: null,
    messages: [msg(1, "agent", "Hello from Riley"), msg(2, "customer", "Hi Riley")],
    ...overrides,
  };
}

async function type(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByLabelText("Message"), text);
}

describe("TestChat", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    getTokenMock.mockReset().mockResolvedValue("token-123");
    sendMock.mockReset();
    endMock.mockReset();
    transcriptMock.mockReset();
  });

  it("explains the test chat before the first message", () => {
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    expect(screen.getByText(/private test conversation/i)).toBeInTheDocument();
    expect(screen.getByText(/real AI/i, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText(/not a phone call/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "End chat" })).not.toBeInTheDocument();
  });

  it("starts a chat without an interaction id and puts ?chat in the URL", async () => {
    sendMock.mockResolvedValue({ kind: "ok", data: turn() });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "When do you open?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(sendMock).toHaveBeenCalledTimes(1));
    expect(sendMock).toHaveBeenCalledWith("token-123", {
      interactionId: null,
      message: "When do you open?",
    });
    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith(`/dashboard/agent?chat=${ID}`, { scroll: false }),
    );
  });

  it("renders the greeting first, with speaker labels", async () => {
    sendMock.mockResolvedValue({ kind: "ok", data: turn() });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "When do you open?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const items = await screen.findAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("RileyHi, thanks for contacting Acme!");
    expect(items[1]).toHaveTextContent("YouWhen do you open?");
    expect(items[2]).toHaveTextContent("RileyWe open at nine.");
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("sends the existing interaction id on later messages and does not rewrite the URL", async () => {
    sendMock.mockResolvedValueOnce({ kind: "ok", data: turn() });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "First");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findAllByRole("listitem");
    replaceMock.mockClear();

    sendMock.mockResolvedValueOnce({
      kind: "ok",
      data: turn({ messages: [msg(4, "customer", "Second"), msg(5, "agent", "Okay.")] }),
    });
    await type(user, "Second");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(sendMock).toHaveBeenCalledTimes(2));
    expect(sendMock).toHaveBeenLastCalledWith("token-123", { interactionId: ID, message: "Second" });
    expect(await screen.findByText("Okay.")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("sends on Enter and adds a newline on Shift+Enter", async () => {
    sendMock.mockResolvedValue({ kind: "ok", data: turn() });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "line one");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await type(user, "line two");
    expect(sendMock).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Message")).toHaveValue("line one\nline two");

    await user.keyboard("{Enter}");
    await waitFor(() => expect(sendMock).toHaveBeenCalledTimes(1));
    expect(sendMock.mock.calls[0]![1].message).toBe("line one\nline two");
  });

  it("disables the composer and shows a typing state while the request is in flight", async () => {
    let resolve: (value: unknown) => void = () => {};
    sendMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "Hello");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/Riley is typing/)).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();

    resolve({ kind: "ok", data: turn() });
    await screen.findAllByRole("listitem");
    expect(screen.queryByText(/Riley is typing/)).not.toBeInTheDocument();
  });

  it("does not send an empty or whitespace-only message", async () => {
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await type(user, "   ");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(sendMock).not.toHaveBeenCalled();
  });

  it("marks a fallback reply with a quiet note", async () => {
    sendMock.mockResolvedValue({
      kind: "ok",
      data: turn({
        fallback: true,
        messages: [
          msg(1, "agent", "Hi!"),
          msg(2, "customer", "Hmm"),
          {
            ...msg(3, "agent", "Sorry, I'm having trouble responding right now. Please try again in a moment."),
            fallback: true,
          },
        ],
      }),
    });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "Hmm");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const items = await screen.findAllByRole("listitem");
    expect(items[2]).toHaveTextContent(FALLBACK_NOTE);
    expect(items[0]).not.toHaveTextContent(FALLBACK_NOTE);
  });

  it("shows the fallback note on a restored transcript", () => {
    render(
      <TestChat
        agentName="Riley"
        chat={{
          kind: "restored",
          transcript: transcript({
            messages: [msg(1, "agent", "Hello"), { ...msg(2, "agent", "Sorry."), fallback: true }],
          }),
        }}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items[1]).toHaveTextContent(FALLBACK_NOTE);
    expect(items[0]).not.toHaveTextContent(FALLBACK_NOTE);
  });

  it("keeps the chat open and the draft on a busy 409, with Retry", async () => {
    sendMock.mockResolvedValueOnce({ kind: "busy" });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: transcript() }} />);

    await type(user, "Second try");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Still working on your last message. Try again.")).toBeInTheDocument();
    expect(screen.queryByText("This chat has ended.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toHaveValue("Second try");

    sendMock.mockResolvedValueOnce({ kind: "ok", data: turn({ messages: [msg(3, "customer", "Second try"), msg(4, "agent", "Done.")] }) });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Done.")).toBeInTheDocument();
  });

  it("shows the quota notice with a usage link when the latest turn is usage limited", async () => {
    sendMock.mockResolvedValueOnce({
      kind: "ok",
      data: turn({
        fallback: true,
        usage_limited: true,
        messages: [msg(1, "customer", "Hi"), { ...msg(2, "agent", "Sorry."), fallback: true }],
      }),
    });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);
    expect(screen.queryByText(USAGE_LIMITED_NOTE, { exact: false })).not.toBeInTheDocument();

    await type(user, "Hi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(USAGE_LIMITED_NOTE, { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View usage" })).toHaveAttribute(
      "href",
      "/dashboard/settings/usage",
    );
  });

  it("clears the quota notice once a later turn is not usage limited", async () => {
    sendMock.mockResolvedValueOnce({ kind: "ok", data: turn({ usage_limited: true, fallback: true }) });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);
    await type(user, "Hi");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(USAGE_LIMITED_NOTE, { exact: false });

    sendMock.mockResolvedValueOnce({ kind: "ok", data: turn({ messages: [msg(4, "agent", "Back.")] }) });
    await type(user, "Again");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Back.")).toBeInTheDocument();
    expect(screen.queryByText(USAGE_LIMITED_NOTE, { exact: false })).not.toBeInTheDocument();
  });

  it("keeps the draft and shows a notice when too many conversations are active", async () => {
    sendMock.mockResolvedValueOnce({ kind: "concurrency_limited" });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "Hello there");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(CONCURRENCY_NOTE)).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toHaveValue("Hello there");
    expect(screen.queryByText("This chat has ended.")).not.toBeInTheDocument();

    sendMock.mockResolvedValueOnce({ kind: "ok", data: turn() });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("We open at nine.")).toBeInTheDocument();
    expect(screen.queryByText(CONCURRENCY_NOTE)).not.toBeInTheDocument();
  });

  it("recovers from the transcript instead of offering a resend when the turn was committed", async () => {
    sendMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    transcriptMock.mockResolvedValue({
      kind: "ok",
      data: transcript({
        messages: [
          msg(1, "agent", "Hello from Riley"),
          msg(2, "customer", "Hi Riley"),
          msg(3, "customer", "Committed"),
          msg(4, "agent", "Got it."),
        ],
      }),
    });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: transcript() }} />);

    await type(user, "Committed");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Got it.")).toBeInTheDocument();
    expect(transcriptMock).toHaveBeenCalledWith("token-123", ID);
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("keeps the draft and Retry when the transcript shows nothing new", async () => {
    sendMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    transcriptMock.mockResolvedValue({ kind: "ok", data: transcript() });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: transcript() }} />);

    await type(user, "Lost");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toHaveValue("Lost");
  });

  it("offers Retry when ending the chat fails to reach the API", async () => {
    endMock.mockResolvedValueOnce({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: transcript() }} />);

    await user.click(screen.getByRole("button", { name: "End chat" }));
    endMock.mockResolvedValueOnce({ kind: "ok", data: transcript({ ended: true, status: "ended" }) });
    await user.click(await screen.findByRole("button", { name: "Retry" }));

    expect(await screen.findByText("This chat has ended.")).toBeInTheDocument();
  });

  it("ends the chat and offers New chat, which clears ?chat", async () => {
    endMock.mockResolvedValue({ kind: "ok", data: transcript({ ended: true, status: "ended" }) });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: transcript() }} />);

    await user.click(screen.getByRole("button", { name: "End chat" }));

    expect(await screen.findByText("This chat has ended.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New chat" })).toHaveFocus();
    expect(endMock).toHaveBeenCalledWith("token-123", ID);
    expect(screen.queryByLabelText("Message")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(replaceMock).toHaveBeenCalledWith("/dashboard/agent", { scroll: false });
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeEnabled();
  });

  it("shows the ended state when the API answers 409", async () => {
    sendMock.mockResolvedValue({ kind: "ended" });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: transcript() }} />);

    await type(user, "Anyone there?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("This chat has ended.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New chat" })).toBeInTheDocument();
  });

  it("links to billing on a 402", async () => {
    sendMock.mockResolvedValue({ kind: "subscription_required" });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "Hello");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const link = await screen.findByRole("link", { name: "Choose a plan" });
    expect(link).toHaveAttribute("href", "/billing/plan");
  });

  it("keeps the typed message and offers a retry when the API is unreachable", async () => {
    sendMock.mockResolvedValueOnce({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);

    await type(user, "Keep me");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/Could not reach the API/)).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toHaveValue("Keep me");
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();

    sendMock.mockResolvedValueOnce({ kind: "ok", data: turn() });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findAllByRole("listitem");
    expect(sendMock).toHaveBeenCalledTimes(2);
    expect(sendMock.mock.calls[1]![1].message).toBe("Keep me");
  });

  it("restores a conversation from a transcript, in order", () => {
    render(<TestChat agentName="Riley" chat={{ kind: "restored", transcript: transcript() }} />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("RileyHello from Riley");
    expect(items[1]).toHaveTextContent("YouHi Riley");
    expect(screen.queryByText(/private test conversation/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "End chat" })).toBeInTheDocument();
  });

  it("restores an ended transcript straight into the ended state", () => {
    render(
      <TestChat
        agentName="Riley"
        chat={{ kind: "restored", transcript: transcript({ ended: true, status: "ended" }) }}
      />,
    );

    expect(screen.getByText("This chat has ended.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New chat" })).toBeInTheDocument();
  });

  it("falls back to an empty chat with a quiet notice when the chat is unavailable", () => {
    render(<TestChat agentName="Riley" chat={{ kind: "unavailable", reason: "not_found" }} />);

    expect(screen.getByText(/could not be found/i)).toBeInTheDocument();
    expect(screen.getByText(/private test conversation/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeEnabled();
  });

  it("announces new messages politely", () => {
    render(<TestChat agentName="Riley" chat={{ kind: "none" }} />);
    expect(screen.getByRole("log")).toHaveAttribute("aria-live", "polite");
  });
});
