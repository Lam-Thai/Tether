"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { Inbox as InboxIcon, MessageCircleQuestion } from "lucide-react";

import type { InboxQuery, InboxType, InteractionListResult, InteractionRow } from "@/lib/api/client";
import { inboxHref, interactionHref, withFilters } from "@/lib/inbox/query";
import { Button, buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Eyebrow } from "@/components/ui/eyebrow";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { AttentionBadge, EscalationBadge, needsStatusMark, OutcomeBadge, StatusBadge } from "./badges";
import { callerLabel, formatBusinessTime, intentLabel } from "./labels";

const TABS: { type: InboxType; label: string }[] = [
  { type: "all", label: "All" },
  { type: "bookings", label: "Bookings" },
  { type: "faqs", label: "FAQs" },
  { type: "attention", label: "Needs attention" },
  { type: "escalations", label: "Escalations" },
  { type: "failed", label: "Failed" },
];

function hasFilters(query: InboxQuery): boolean {
  return Boolean(
    (query.type && query.type !== "all") || query.status || query.intent || query.from || query.to,
  );
}

/**
 * Presentational view of `/dashboard/inbox`: switches exhaustively on the
 * `GET /interactions` result. Loading lives in the route's `loading.tsx`.
 * Filters live in the URL: tabs are links, the date range pushes the query.
 */
export function InboxView({ result, query }: { result: InteractionListResult; query: InboxQuery }) {
  switch (result.kind) {
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
    case "unreachable":
      return (
        <Shell query={query}>
          <Callout tone="danger" title="Can't load your inbox">
            {result.reason} Reload the page to try again.
          </Callout>
        </Shell>
      );
    case "validation":
      return (
        <Shell query={query}>
          <Callout
            tone="warning"
            title="Those filters don't work"
            action={
              <Link href="/dashboard/inbox" className={buttonVariants({ variant: "outline", size: "sm" })}>
                Clear filters
              </Link>
            }
          >
            {result.message}
          </Callout>
        </Shell>
      );
    case "ok": {
      const { items, next_cursor: nextCursor, timezone } = result.data;
      return (
        <Shell query={query}>
          {items.length === 0 ? (
            <EmptyState query={query} />
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                Times are in <span className="font-mono">{timezone}</span>, your business timezone.
              </p>
              <ul aria-label="Conversations" className="flex flex-col gap-3">
                {items.map((row, i) => (
                  <li key={row.id} className="animate-enter" style={{ "--i": Math.min(i, 8) } as React.CSSProperties}>
                    <InboxRow row={row} timezone={timezone} query={query} />
                  </li>
                ))}
              </ul>
            </>
          )}
          {(nextCursor || query.cursor) && (
            <nav aria-label="Pages" className="flex flex-wrap items-center gap-3">
              {query.cursor && (
                <Link
                  href={inboxHref(withFilters(query, {}))}
                  className={buttonVariants({ variant: "outline", size: "sm" })}
                >
                  Back to newest
                </Link>
              )}
              {nextCursor && (
                <Link
                  href={inboxHref({ ...query, cursor: nextCursor })}
                  className={buttonVariants({ variant: "outline", size: "sm" })}
                >
                  Older
                </Link>
              )}
            </nav>
          )}
        </Shell>
      );
    }
  }
}

function Shell({ query, children }: { query: InboxQuery; children: React.ReactNode }) {
  return (
    <div className="flex w-full max-w-[1280px] flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Eyebrow>Conversations</Eyebrow>
        <h1 className="text-h1 font-bold tracking-tight uppercase">Inbox</h1>
        <p className="max-w-[72ch] text-base text-muted-foreground">
          Every conversation your receptionist has had, with a two-sentence summary and what was
          actually done.
        </p>
      </div>
      <Filters query={query} />
      {children}
    </div>
  );
}

function Filters({ query }: { query: InboxQuery }) {
  const router = useRouter();
  const active = query.type ?? "all";

  function applyDates(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const from = String(data.get("from") ?? "");
    const to = String(data.get("to") ?? "");
    router.push(inboxHref(withFilters(query, { from, to })));
  }

  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <nav aria-label="Filter by type">
        <ul className="flex flex-wrap gap-2">
          {TABS.map((tab) => {
            const current = tab.type === active;
            return (
              <li key={tab.type}>
                <Link
                  href={inboxHref(withFilters(query, { type: tab.type }))}
                  aria-current={current ? "page" : undefined}
                  className={cn(
                    "inline-flex h-9 items-center rounded-md border-[1.5px] px-3 text-sm font-medium tracking-[0.02em] transition-[background-color] duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring motion-reduce:transition-none",
                    current
                      ? "border-foreground bg-foreground text-background"
                      : "border-input bg-transparent text-foreground hover:bg-muted",
                  )}
                >
                  {tab.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      {/* Keyed by the applied range so the inputs follow the URL after Back or Clear. */}
      <form
        key={`${query.from ?? ""}|${query.to ?? ""}`}
        onSubmit={applyDates}
        aria-label="Filter by date"
        className="flex flex-wrap items-end gap-3"
      >
        <div className="flex flex-col gap-1">
          <Label htmlFor="inbox-from">From</Label>
          <Input id="inbox-from" name="from" type="date" defaultValue={query.from ?? ""} className="h-9 w-40" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="inbox-to">To</Label>
          <Input id="inbox-to" name="to" type="date" defaultValue={query.to ?? ""} className="h-9 w-40" />
        </div>
        <Button type="submit" variant="outline" size="sm">
          Apply dates
        </Button>
        {(query.from || query.to) && (
          <Link
            href={inboxHref(withFilters(query, { from: "", to: "" }))}
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            Clear dates
          </Link>
        )}
      </form>
    </div>
  );
}

function EmptyState({ query }: { query: InboxQuery }) {
  if (hasFilters(query) || query.cursor) {
    return (
      <Callout
        tone="warning"
        title="No results for these filters"
        action={
          <Link href="/dashboard/inbox" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Clear filters
          </Link>
        }
      >
        <span className="flex items-start gap-2">
          <MessageCircleQuestion aria-hidden className="mt-0.5 size-4 shrink-0" />
          No conversations match. Try another type or a wider date range.
        </span>
      </Callout>
    );
  }
  return (
    <div className="flex max-w-xl flex-col items-start gap-3 rounded-md border border-border bg-card p-6 shadow-card">
      <InboxIcon aria-hidden className="size-6" />
      <h2 className="text-h2 font-bold tracking-tight uppercase">No conversations yet</h2>
      <p className="text-base text-muted-foreground">
        Conversations show up here once your receptionist handles one. Start a test chat to see the
        first.
      </p>
      <Link href="/dashboard/agent" className={buttonVariants({ size: "sm" })}>
        Open the test chat
      </Link>
    </div>
  );
}

function InboxRow({ row, timezone, query }: { row: InteractionRow; timezone: string; query: InboxQuery }) {
  const time = formatBusinessTime(row.started_at, timezone);
  return (
    <article className="relative flex flex-col gap-2 rounded-md border border-border bg-card p-4 shadow-card transition-[box-shadow] duration-200 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ring hover:shadow-lift motion-reduce:transition-none">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link
          href={interactionHref(row.id, query)}
          className="font-mono text-sm font-medium after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
        >
          <time dateTime={row.started_at}>{time}</time>
          <span className="sr-only"> - open conversation</span>
        </Link>
        <span className="text-base font-bold">{callerLabel(row)}</span>
        <span className="text-sm text-muted-foreground">{intentLabel(row.intent)}</span>
      </div>
      {row.summary ? (
        <p className="max-w-[72ch] text-base">{row.summary}</p>
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
  );
}
