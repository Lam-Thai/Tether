"use client";

import { useRef, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { LoaderCircle, TriangleAlert } from "lucide-react";

import {
  listInteractionMessages,
  type InteractionMessagePage,
  type InteractionMessagesResult,
  type MessageRead,
} from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { cn } from "@/lib/utils";
import { formatBusinessTime } from "./labels";

export const FALLBACK_NOTE = "Fallback reply: the receptionist could not answer properly.";

type Load = { kind: "idle" } | { kind: "loading" } | { kind: "error"; message: string };

function failureMessage(result: Exclude<InteractionMessagesResult, { kind: "ok" }>): string {
  switch (result.kind) {
    case "unauthorized":
      return "Your session has expired. Sign in again to see more.";
    case "subscription_required":
      return "Choose a plan to see more of this conversation.";
    case "not_found":
      return "This conversation is no longer available.";
    case "unreachable":
      return result.reason;
  }
}

/**
 * The stored transcript. The first page arrives from the server; "Load more"
 * appends the next page from `next_after`. A response that arrives after a
 * newer request started (or for a different position) is dropped.
 */
export function Transcript({
  interactionId,
  timezone,
  messageCount,
  initial,
}: {
  interactionId: string;
  timezone: string;
  messageCount: number;
  initial: InteractionMessagesResult;
}) {
  const { getToken } = useAuth();
  const first: InteractionMessagePage | null = initial.kind === "ok" ? initial.data : null;
  const [items, setItems] = useState<MessageRead[]>(first?.items ?? []);
  // `null` means the end of the transcript; 0 means nothing has loaded yet.
  const [nextAfter, setNextAfter] = useState<number | null>(first ? first.next_after : 0);
  const [load, setLoad] = useState<Load>(
    initial.kind === "ok" ? { kind: "idle" } : { kind: "error", message: failureMessage(initial) },
  );
  const requestRef = useRef(0);
  const afterRef = useRef<number | null>(nextAfter);

  async function loadMore() {
    const after = afterRef.current;
    if (after === null) return;
    const request = ++requestRef.current;
    setLoad({ kind: "loading" });

    const token = await getToken();
    if (request !== requestRef.current) return;
    if (!token) {
      setLoad({ kind: "error", message: "Your session has expired. Sign in again to see more." });
      return;
    }

    const result = await listInteractionMessages(token, interactionId, after);
    // Stale: a newer request started, or the transcript already moved past `after`.
    if (request !== requestRef.current || afterRef.current !== after) return;

    if (result.kind !== "ok") {
      setLoad({ kind: "error", message: failureMessage(result) });
      return;
    }
    setItems((current) => {
      const seen = new Set(current.map((m) => m.sequence_number));
      return [...current, ...result.data.items.filter((m) => !seen.has(m.sequence_number))];
    });
    afterRef.current = result.data.next_after;
    setNextAfter(result.data.next_after);
    setLoad({ kind: "idle" });
  }

  const loading = load.kind === "loading";

  return (
    <section aria-labelledby="transcript-heading" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="transcript-heading" className="text-h2 font-bold tracking-tight uppercase">
          Transcript
        </h2>
        <p className="text-sm text-muted-foreground">
          {items.length} of {messageCount} {messageCount === 1 ? "message" : "messages"}
        </p>
      </div>

      {items.length === 0 && nextAfter === null && load.kind !== "error" && (
        <p className="text-base text-muted-foreground">No messages were stored for this conversation.</p>
      )}

      {items.length > 0 && (
        <ol aria-label="Messages" className="flex flex-col gap-4">
          {items.map((message) => (
            <TranscriptMessage key={message.sequence_number} message={message} timezone={timezone} />
          ))}
        </ol>
      )}

      {load.kind === "error" && (
        <Callout
          tone="danger"
          title="Can't load the transcript"
          compact
          headingLevel={3}
          action={
            nextAfter !== null && (
              <Button type="button" variant="outline" size="sm" onClick={loadMore}>
                Try again
              </Button>
            )
          }
        >
          {load.message}
        </Callout>
      )}

      {nextAfter !== null && load.kind !== "error" && (
        <div>
          <Button type="button" variant="outline" size="sm" onClick={loadMore} disabled={loading}>
            {loading && <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" />}
            {loading ? "Loading" : "Load more"}
          </Button>
        </div>
      )}
    </section>
  );
}

function TranscriptMessage({ message, timezone }: { message: MessageRead; timezone: string }) {
  const isAgent = message.speaker === "agent";
  return (
    <li className={cn("flex flex-col gap-1", !isAgent && "items-end")}>
      <span className="flex items-baseline gap-2 text-sm font-medium tracking-[0.02em] text-muted-foreground">
        <span>{isAgent ? "Agent" : "Customer"}</span>
        <time dateTime={message.created_at} className="font-mono text-xs">
          {formatBusinessTime(message.created_at, timezone)}
        </time>
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
