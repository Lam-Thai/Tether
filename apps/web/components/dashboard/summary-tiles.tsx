import Link from "next/link";
import { BellRing, CalendarCheck, CircleCheck, Inbox } from "lucide-react";

import type { DashboardSummaryResult } from "@/lib/api/client";
import { Callout } from "@/components/ui/callout";
import { Card } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { rangeSummary } from "./integration-labels";

function formatCount(value: number): string {
  return value.toLocaleString("en-US");
}

/**
 * The four headline numbers for the home page. Switches exhaustively on the
 * `GET /dashboard/summary` result; a failure replaces only this block.
 */
export function SummaryTiles({ result }: { result: DashboardSummaryResult }) {
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
          Choose a plan to see your numbers.
        </Callout>
      );
    case "validation":
      return (
        <Callout tone="warning" title="Can't show these numbers">
          {result.message}
        </Callout>
      );
    case "unreachable":
      return (
        <Callout tone="danger" title="Can't load your numbers">
          {result.reason} Reload the page to try again.
        </Callout>
      );
    case "ok": {
      const { data } = result;
      const range = rangeSummary(data.from, data.to);
      const total = data.total_interactions;
      return (
        <section aria-label="Summary" className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            <span className="font-semibold text-foreground">{range.title}</span>
            {": "}
            <span className="font-mono">{range.dates}</span>
            {", "}
            <span className="font-mono">{data.timezone}</span>
          </p>
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Tile Icon={Inbox} label="Total interactions" value={total} note={total === 0 ? "None in this range yet." : undefined} />
            <Tile Icon={CalendarCheck} label="Successful bookings" value={data.successful_bookings} />
            <Tile Icon={CircleCheck} label="FAQs answered" value={data.faqs_answered} />
            <AttentionTile count={data.open_escalations} />
          </dl>
        </section>
      );
    }
  }
}

function Tile({
  Icon,
  label,
  value,
  note,
}: {
  Icon: typeof Inbox;
  label: string;
  value: number;
  note?: string;
}) {
  return (
    <Card className="gap-2 p-5">
      <dt className="flex items-center gap-2 text-sm font-medium tracking-[0.02em] text-muted-foreground">
        <Icon aria-hidden className="size-4 shrink-0" />
        {label}
      </dt>
      <dd className="font-mono text-h1 font-bold">{formatCount(value)}</dd>
      {note && <dd className="text-sm text-muted-foreground">{note}</dd>}
    </Card>
  );
}

/** Open escalations are a live queue, so the tile links to it and says it ignores the range. */
function AttentionTile({ count }: { count: number }) {
  const open = count > 0;
  return (
    <Card
      className={cn(
        "relative gap-2 border-l-[6px] p-5 transition-[box-shadow] duration-200 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ring hover:shadow-lift motion-reduce:transition-none",
        open ? "border-l-danger" : "border-l-success",
      )}
    >
      <dt className="flex items-center gap-2 text-sm font-medium tracking-[0.02em] text-muted-foreground">
        {open ? (
          <BellRing aria-hidden className="size-4 shrink-0 text-danger-foreground" />
        ) : (
          <CircleCheck aria-hidden className="size-4 shrink-0" />
        )}
        Needs attention
      </dt>
      <dd className="font-mono text-h1 font-bold">{formatCount(count)}</dd>
      <dd className="flex flex-col gap-1 text-sm text-muted-foreground">
        {open ? "Open now, whatever the date range." : "Nothing waiting for you."}
        <Link
          href="/dashboard/inbox?type=attention"
          className="w-fit font-medium text-link underline after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
        >
          {open ? "Review in the inbox" : "Open the inbox"}
        </Link>
      </dd>
    </Card>
  );
}
