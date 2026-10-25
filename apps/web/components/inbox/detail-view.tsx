import Link from "next/link";
import { ArrowLeft, Check, X } from "lucide-react";

import type {
  InteractionDetail,
  InteractionDetailResult,
  InteractionMessagesResult,
  OperationItem,
} from "@/lib/api/client";
import { buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Eyebrow } from "@/components/ui/eyebrow";
import { EscalationBadge, needsStatusMark, OutcomeBadge, StatusBadge } from "./badges";
import { callerLabel, formatBusinessTime, intentLabel } from "./labels";
import { Transcript } from "./transcript";

function BackLink({ href }: { href: string }) {
  return (
    <Link href={href} className={buttonVariants({ variant: "outline", size: "sm", className: "w-fit" })}>
      <ArrowLeft aria-hidden />
      Back to inbox
    </Link>
  );
}

/**
 * Presentational view of `/dashboard/inbox/[id]`: switches exhaustively on the
 * detail result. `messages` is the first transcript page, fetched alongside.
 */
export function InteractionDetailView({
  detail,
  messages,
  backHref,
}: {
  detail: InteractionDetailResult;
  messages: InteractionMessagesResult;
  backHref: string;
}) {
  switch (detail.kind) {
    case "ok":
      return <DetailBody detail={detail.data} messages={messages} backHref={backHref} />;
    case "not_found":
      return (
        <div className="flex max-w-3xl flex-col gap-4">
          <BackLink href={backHref} />
          <Callout tone="warning" title="Conversation not found" headingLevel={1}>
            This conversation doesn&apos;t exist or isn&apos;t part of your business.
          </Callout>
        </div>
      );
    case "unreachable":
      return (
        <div className="flex max-w-3xl flex-col gap-4">
          <BackLink href={backHref} />
          <Callout tone="danger" title="Can't load this conversation" headingLevel={1}>
            {detail.reason} Reload the page to try again.
          </Callout>
        </div>
      );
    case "unauthorized":
      return (
        <div className="max-w-3xl">
          <Callout tone="danger" title="Signed out" headingLevel={1}>
            Your session has expired. <Link href="/sign-in" className="underline">Sign in again</Link>.
          </Callout>
        </div>
      );
    case "subscription_required":
      return (
        <div className="max-w-3xl">
          <Callout
            tone="warning"
            title="Plan required"
            headingLevel={1}
            action={
              <Link href="/billing/plan" className={buttonVariants({ size: "sm" })}>
                Choose plan
              </Link>
            }
          >
            Choose a plan to see your inbox.
          </Callout>
        </div>
      );
  }
}

function DetailBody({
  detail,
  messages,
  backHref,
}: {
  detail: InteractionDetail;
  messages: InteractionMessagesResult;
  backHref: string;
}) {
  const started = formatBusinessTime(detail.started_at, detail.timezone, { long: true });
  return (
    <div className="flex w-full max-w-4xl flex-col gap-6">
      <BackLink href={backHref} />

      <div className="flex flex-col gap-2">
        <Eyebrow>
          {intentLabel(detail.intent)} <span aria-hidden>·</span> <time dateTime={detail.started_at}>{started}</time>
        </Eyebrow>
        <h1 className="text-h1 font-bold tracking-tight uppercase">{callerLabel(detail)}</h1>
        <div className="flex flex-wrap items-center gap-2">
          {detail.outcome && <OutcomeBadge outcome={detail.outcome} />}
          {needsStatusMark(detail.status, detail.outcome) && <StatusBadge status={detail.status} />}
          {detail.requires_human && <EscalationBadge />}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle as="h2">{summaryLabel(detail)}</CardTitle>
        </CardHeader>
        <CardContent>
          {detail.summary ? (
            <p className="max-w-[72ch] text-base">{detail.summary}</p>
          ) : (
            <p className="max-w-[72ch] text-base text-muted-foreground">Summary pending</p>
          )}
        </CardContent>
      </Card>

      <section aria-labelledby="actions-heading" className="flex flex-col gap-3">
        <h2 id="actions-heading" className="text-h2 font-bold tracking-tight uppercase">
          Verified actions
        </h2>
        {detail.operations.length === 0 ? (
          <p className="text-base text-muted-foreground">
            The receptionist took no actions in this conversation.
          </p>
        ) : (
          <ol aria-label="Actions" className="flex flex-wrap items-center gap-x-2 gap-y-2 text-sm">
            {detail.operations.map((op, i) => (
              <li key={op.id} className="flex items-center gap-2">
                {i > 0 && <span aria-hidden className="text-muted-foreground">·</span>}
                <OperationChip op={op} />
              </li>
            ))}
          </ol>
        )}
      </section>

      <Transcript
        key={detail.id}
        interactionId={detail.id}
        timezone={detail.timezone}
        messageCount={detail.message_count}
        initial={messages}
      />
    </div>
  );
}

function summaryLabel(detail: Pick<InteractionDetail, "summary" | "summary_source">): string {
  if (detail.summary_source === "ai") return "AI summary";
  if (detail.summary_source === "template") return "Automatic summary";
  return "Summary";
}

const OP_TEXT = { succeeded: "succeeded", rejected: "rejected", failed: "failed" } as const;

function OperationChip({ op }: { op: OperationItem }) {
  const ok = op.status === "succeeded";
  const Icon = ok ? Check : X;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-border bg-card px-2 py-1">
      <code className="font-mono">{op.tool_name}</code>
      <Icon aria-hidden className="size-3.5" />
      <span className={ok ? "sr-only" : "font-medium"}>{OP_TEXT[op.status]}</span>
      {op.label && <span className="font-mono text-muted-foreground">{op.label}</span>}
    </span>
  );
}
