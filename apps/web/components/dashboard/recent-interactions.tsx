import Link from "next/link";
import { Inbox } from "lucide-react";

import type { InteractionListResult } from "@/lib/api/client";
import { interactionHref } from "@/lib/inbox/query";
import { AttentionBadge, EscalationBadge, needsStatusMark, OutcomeBadge, StatusBadge } from "@/components/inbox/badges";
import { callerLabel, formatBusinessTime, intentLabel } from "@/components/inbox/labels";
import { buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card } from "@/components/ui/card";

/**
 * The five newest conversations (`GET /interactions?limit=5`), each linking to
 * its inbox detail. A failure shows a Callout here and nowhere else.
 */
export function RecentInteractions({ result }: { result: InteractionListResult }) {
  switch (result.kind) {
    case "unauthorized":
      return (
        <Callout tone="danger" title="Signed out">
          Your session has expired. <Link href="/sign-in" className="underline">Sign in again</Link>.
        </Callout>
      );
    case "subscription_required":
      return (
        <Callout
          tone="warning"
          title="Plan required"
          action={
            <Link href="/billing/plan" className={buttonVariants({ size: "sm" })}>
              Choose plan
            </Link>
          }
        >
          Choose a plan to see your conversations.
        </Callout>
      );
    case "validation":
      return (
        <Callout tone="warning" title="Can't show recent conversations">
          {result.message}
        </Callout>
      );
    case "unreachable":
      return (
        <Callout tone="danger" title="Can't load recent conversations">
          {result.reason} Reload the page to try again.
        </Callout>
      );
    case "ok": {
      const { items, timezone } = result.data;
      if (items.length === 0) {
        return (
          <Card className="items-start gap-3 p-6">
            <Inbox aria-hidden className="size-6" />
            <h3 className="text-lg font-semibold">No conversations yet</h3>
            <p className="max-w-[72ch] text-base text-muted-foreground">
              Calls and chats show up here once your receptionist handles one. Start a test chat to see the first.
            </p>
            <Link href="/dashboard/agent#test-chat" className={buttonVariants({ size: "sm" })}>
              Open the test chat
            </Link>
          </Card>
        );
      }
      return (
        <div className="flex flex-col gap-3">
          <ul aria-label="Recent conversations" className="flex flex-col gap-3">
            {items.map((row) => (
              <li key={row.id}>
                <article className="relative flex flex-col gap-2 rounded-md border border-border bg-card p-4 shadow-card transition-[box-shadow] duration-200 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ring hover:shadow-lift motion-reduce:transition-none">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <Link
                      href={interactionHref(row.id, {})}
                      className="font-mono text-sm font-medium after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
                    >
                      <time dateTime={row.started_at}>{formatBusinessTime(row.started_at, timezone)}</time>
                      <span className="sr-only"> - open conversation</span>
                    </Link>
                    <span className="text-base font-bold">{callerLabel(row)}</span>
                    <span className="text-sm text-muted-foreground">{intentLabel(row.intent)}</span>
                  </div>
                  {row.summary ? (
                    <p className="line-clamp-2 max-w-[72ch] text-base">{row.summary}</p>
                  ) : (
                    <p className="max-w-[72ch] text-base text-muted-foreground">Summary pending</p>
                  )}
                  <div className="flex flex-wrap items-center gap-2">
                    {row.outcome && <OutcomeBadge outcome={row.outcome} />}
                    {needsStatusMark(row.status, row.outcome) && <StatusBadge status={row.status} />}
                    {row.needs_attention ? (
                      <AttentionBadge reason={row.escalation?.reason} />
                    ) : (
                      row.requires_human && !row.escalation && <EscalationBadge />
                    )}
                  </div>
                </article>
              </li>
            ))}
          </ul>
          <Link href="/dashboard/inbox" className={buttonVariants({ variant: "outline", size: "sm" }) + " w-fit"}>
            View all conversations
          </Link>
        </div>
      );
    }
  }
}
