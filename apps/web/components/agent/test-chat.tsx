"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import { MessageSquare, TriangleAlert } from "lucide-react";

import {
  endTestChat,
  getTestTranscript,
  sendTestMessage,
  type MessageRead,
  type TranscriptRead,
} from "@/lib/api/client";
import { AGENT_LIMITS, testMessageSchema } from "@/lib/validation/agent";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Callout } from "@/components/ui/callout";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Label } from "@/components/ui/label";
import { controlClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const CHAT_PATH = "/dashboard/agent";
const SESSION_EXPIRED = "Your session has expired. Please sign in again.";
export const FALLBACK_NOTE =
  "The receptionist could not answer properly. This is a fallback message.";

export const USAGE_LIMITED_NOTE =
  "Today's AI limit is reached, so replies use the safe fallback until 00:00 UTC.";
export const CONCURRENCY_NOTE =
  "Too many conversations are active right now. End one, or try again in a few minutes.";

/** What the server could (or could not) restore from `?chat=`. */
export type ChatLoad =
  | { kind: "none" }
  | { kind: "restored"; transcript: TranscriptRead }
  | { kind: "unavailable"; reason: "not_found" | "unreachable" };

type ChatError =
  | { kind: "message"; text: string }
  | { kind: "subscription_required" }
  | { kind: "retry"; text: string }
  | { kind: "busy" }
  | { kind: "concurrency_limited" }
  | { kind: "retry_end"; text: string };

export function TestChat({ agentName, chat }: { agentName: string; chat: ChatLoad }) {
  const { getToken } = useAuth();
  const router = useRouter();

  const restored = chat.kind === "restored" ? chat.transcript : null;
  const [interactionId, setInteractionId] = useState<string | null>(restored?.interaction_id ?? null);
  const [messages, setMessages] = useState<MessageRead[]>(restored?.messages ?? []);
  const [ended, setEnded] = useState(restored?.ended ?? false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [ending, setEnding] = useState(false);
  const [error, setError] = useState<ChatError | null>(null);
  // From the latest turn response only; not persisted, so a reload does not show it.
  const [usageLimited, setUsageLimited] = useState(false);
  const [notice, setNotice] = useState<string | null>(
    chat.kind === "unavailable"
      ? chat.reason === "not_found"
        ? "That chat could not be found, so this is a new one."
        : "That chat could not be loaded right now. You can start a new one."
      : null,
  );

  const logRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const newChatRef = useRef<HTMLButtonElement>(null);
  const focusNewChat = useRef(false);

  // After the owner ends the chat the composer disappears, so move focus to
  // the control that replaces it.
  useEffect(() => {
    if (ended && focusNewChat.current) {
      focusNewChat.current = false;
      newChatRef.current?.focus();
    }
  }, [ended]);

  // Keep the newest message (or the typing row) in view. Jump instead of
  // smooth-scrolling, so reduced-motion users never get animated movement.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  useEffect(() => {
    if (!pending) composerRef.current?.focus({ preventScroll: true });
  }, [pending]);

  const parsed = testMessageSchema.safeParse(draft);
  const tooLong = draft.trim().length > AGENT_LIMITS.message;
  const canSend = parsed.success && !pending && !ended;

  function startNewChat() {
    setInteractionId(null);
    setMessages([]);
    setEnded(false);
    setDraft("");
    setError(null);
    setNotice(null);
    setUsageLimited(false);
    router.replace(CHAT_PATH, { scroll: false });
  }

  async function send() {
    if (!canSend || !parsed.success) return;

    setError(null);
    setNotice(null);
    setPending(true);
    try {
      const token = await getToken();
      if (!token) {
        setError({ kind: "message", text: SESSION_EXPIRED });
        return;
      }

      const result = await sendTestMessage(token, { interactionId, message: parsed.data });

      switch (result.kind) {
        case "ok": {
          const turn = result.data;
          const isNewChat = interactionId === null;
          setInteractionId(turn.interaction_id);
          setMessages((current) => [...current, ...turn.messages]);
          setEnded(turn.ended);
          setUsageLimited(turn.usage_limited);
          setDraft("");
          if (isNewChat) {
            router.replace(`${CHAT_PATH}?chat=${turn.interaction_id}`, { scroll: false });
          }
          return;
        }
        case "ended":
          setEnded(true);
          return;
        case "busy":
          setError({ kind: "busy" });
          return;
        case "concurrency_limited":
          setError({ kind: "concurrency_limited" });
          return;
        case "not_found":
          setError({ kind: "message", text: "This chat could not be found. Start a new chat." });
          setEnded(true);
          return;
        case "subscription_required":
          setError({ kind: "subscription_required" });
          return;
        case "validation":
          setError({ kind: "message", text: result.message });
          return;
        case "unauthorized":
          setError({ kind: "message", text: SESSION_EXPIRED });
          return;
        case "unreachable": {
          // The outcome is unknown: the server may have committed the turn.
          // Check the transcript before offering a resend.
          if (interactionId) {
            const check = await getTestTranscript(token, interactionId);
            if (check.kind === "ok" && check.data.messages.length > messages.length) {
              setMessages(check.data.messages);
              setEnded(check.data.ended);
              setDraft("");
              return;
            }
          }
          setError({ kind: "retry", text: result.reason });
          return;
        }
      }
    } finally {
      setPending(false);
    }
  }

  async function endChat() {
    if (!interactionId || ending) return;
    setError(null);
    setEnding(true);
    try {
      const token = await getToken();
      if (!token) {
        setError({ kind: "message", text: SESSION_EXPIRED });
        return;
      }
      const result = await endTestChat(token, interactionId);
      switch (result.kind) {
        case "ok":
          focusNewChat.current = true;
          setEnded(true);
          return;
        case "not_found":
          setError({ kind: "message", text: "This chat could not be found. Start a new chat." });
          setEnded(true);
          return;
        case "subscription_required":
          setError({ kind: "subscription_required" });
          return;
        case "unauthorized":
          setError({ kind: "message", text: SESSION_EXPIRED });
          return;
        case "unreachable":
          setError({ kind: "retry_end", text: result.reason });
          return;
      }
    } finally {
      setEnding(false);
    }
  }

  const hasChat = messages.length > 0 || interactionId !== null;

  return (
    <Card className="min-h-[32rem] max-h-[85dvh] gap-0 overflow-hidden">
      <CardHeader className="border-b border-border">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <CardTitle as="h2">Test your receptionist</CardTitle>
            <CardDescription>
              Chatting with {agentName}. Replies come from the live AI.
            </CardDescription>
          </div>
          {interactionId && !ended && (
            <Button variant="outline" size="sm" onClick={endChat} disabled={ending || pending}>
              {ending ? "Ending…" : "End chat"}
            </Button>
          )}
        </div>
      </CardHeader>

      <CardContent className="flex min-h-0 flex-1 flex-col gap-4 px-0 pt-0 pb-0">
        <div
          ref={logRef}
          role="log"
          aria-live="polite"
          aria-label="Test conversation"
          className="min-h-0 flex-1 overflow-y-auto px-6 py-4"
        >
          {notice && <p className="mb-4 text-sm text-muted-foreground">{notice}</p>}

          {!hasChat && (
            <div className="flex flex-col items-start gap-3 py-6">
              <MessageSquare aria-hidden className="size-8 text-muted-foreground" />
              <p className="text-lg font-semibold">No messages yet</p>
              <p className="max-w-[48ch] text-base text-muted-foreground">
                This is a private test conversation. It uses the real AI, so replies are not
                scripted, and it is not a phone call. Write a message below to start.
              </p>
            </div>
          )}

          <ol className="flex flex-col gap-4">
            {messages.map((message) => (
              <ChatMessage
                key={message.sequence_number}
                message={message}
                agentName={agentName}
              />
            ))}
          </ol>

          {pending && (
            <p role="status" className="mt-4 text-sm text-muted-foreground">
              {agentName} is typing…
            </p>
          )}
        </div>

        <div className="flex flex-col gap-3 border-t border-border px-6 py-4">
          {usageLimited && (
            <Callout tone="warning" title="AI limit reached" headingLevel={3} compact>
              {USAGE_LIMITED_NOTE}{" "}
              <Link href="/dashboard/settings/usage" className="font-medium underline">
                View usage
              </Link>
            </Callout>
          )}
          {error?.kind === "concurrency_limited" && (
            <Callout
              tone="warning"
              title="Too many conversations"
              headingLevel={3}
              compact
              action={
                <button type="button" onClick={send} className="text-sm font-medium underline">
                  Retry
                </button>
              }
            >
              {CONCURRENCY_NOTE}
            </Callout>
          )}
          {error?.kind === "message" && <FormError>{error.text}</FormError>}
          {error?.kind === "subscription_required" && (
            <FormError>
              Your plan is not active, so the test chat is paused.{" "}
              <Link href="/billing/plan" className="font-medium underline">
                Choose a plan
              </Link>
            </FormError>
          )}
          {error?.kind === "busy" && (
            <FormError>
              <span>Still working on your last message. Try again.</span>{" "}
              <button type="button" onClick={send} className="font-medium underline">
                Retry
              </button>
            </FormError>
          )}
          {error?.kind === "retry_end" && (
            <FormError>
              <span>{error.text}</span>{" "}
              <button type="button" onClick={endChat} className="font-medium underline">
                Retry
              </button>
            </FormError>
          )}
          {error?.kind === "retry" && (
            <FormError>
              <span>{error.text} Your message is still in the box.</span>{" "}
              <button type="button" onClick={send} className="font-medium underline">
                Retry
              </button>
            </FormError>
          )}

          {ended ? (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-base text-muted-foreground">This chat has ended.</p>
              <Button ref={newChatRef} onClick={startNewChat}>
                New chat
              </Button>
            </div>
          ) : (
            <form
              className="flex flex-col gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void send();
              }}
            >
              <Label htmlFor="test-chat-message">Message</Label>
              <textarea
                id="test-chat-message"
                ref={composerRef}
                rows={2}
                value={draft}
                disabled={pending}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void send();
                  }
                }}
                aria-invalid={tooLong ? true : undefined}
                aria-describedby="test-chat-hint"
                className={cn(controlClasses, "h-auto resize-none")}
              />
              <div className="flex items-center justify-between gap-4">
                <p id="test-chat-hint" className="text-sm text-muted-foreground">
                  Enter sends. Shift and Enter adds a line.
                </p>
                <Button type="submit" disabled={!canSend}>
                  Send
                </Button>
              </div>
              {tooLong && (
                <FieldError>Messages can be up to {AGENT_LIMITS.message} characters.</FieldError>
              )}
            </form>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ChatMessage({
  message,
  agentName,
}: {
  message: MessageRead;
  agentName: string;
}) {
  const isAgent = message.speaker === "agent";

  return (
    <li className={cn("flex flex-col gap-1", !isAgent && "items-end")}>
      <span className="text-sm font-medium tracking-[0.02em] text-muted-foreground">
        {isAgent ? agentName : "You"}
      </span>
      <p
        className={cn(
          "max-w-[85%] rounded-md border border-border px-4 py-2 text-base whitespace-pre-wrap",
          isAgent ? "bg-card" : "bg-muted",
        )}
      >
        {message.text}
      </p>
      {isAgent && message.fallback && (
        <p className="flex max-w-[85%] items-start gap-1.5 text-sm text-muted-foreground">
          <TriangleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <span>{FALLBACK_NOTE}</span>
        </p>
      )}
    </li>
  );
}
