"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { CircleAlert, TriangleAlert } from "lucide-react";

import type { UsageMeter, UsageRead, UsageResult } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Eyebrow } from "@/components/ui/eyebrow";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const GLOBAL_LIMIT_MESSAGE =
  "Platform limit reached for today. Replies use the safe fallback until reset.";

export const GLOBAL_CONCURRENCY_MESSAGE =
  "The platform is at its limit of active conversations. New chats may be refused for a few minutes; open chats keep working.";

const KNOWN_LABELS: Record<string, string> = {
  gemini_request: "AI replies",
  tts_request: "Voice replies",
  call: "Phone calls",
  sms: "Text messages",
};

/** Known kinds get a plain label; unknown kinds get one derived from the kind string. */
export function meterLabel(kind: string): string {
  const known = KNOWN_LABELS[kind];
  if (known) return known;
  const words = kind.replace(/[_-]+/g, " ").trim().toLowerCase();
  if (!words) return "Other usage";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "0.004210" -> "$0.0042"; cents precision once the amount reaches a cent. */
export function formatEstimatedCost(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount === 0) return "$0.00";
  return amount >= 0.01 ? `$${amount.toFixed(2)}` : `$${amount.toFixed(4)}`;
}

const utcTime = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

const localTime = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

const noopSubscribe = () => () => {};

/** Local time is null on the server and first render, so server and client markup never disagree. */
function useLocalResetTime(resetsAt: string): string | null {
  return useSyncExternalStore(
    noopSubscribe,
    () => {
      const date = new Date(resetsAt);
      return Number.isNaN(date.getTime()) ? null : localTime.format(date);
    },
    () => null,
  );
}

function utcResetText(resetsAt: string): string {
  const date = new Date(resetsAt);
  return Number.isNaN(date.getTime()) ? "00:00" : utcTime.format(date);
}

function isAtLimit(meter: UsageMeter): boolean {
  return meter.used >= meter.limit;
}

function isEmptyDay(data: UsageRead): boolean {
  return (
    data.meters.every((m) => m.used === 0) &&
    data.gemini_tokens === 0 &&
    data.failed_requests === 0 &&
    data.concurrency.active === 0
  );
}

/**
 * Presentational view of `/dashboard/settings/usage`: switches exhaustively on
 * the `GET /usage` result. Loading lives in the route's `loading.tsx`.
 */
export function UsagePanel({ usage }: { usage: UsageResult }) {
  switch (usage.kind) {
    case "ok":
      return <UsageOverview data={usage.data} />;
    case "unreachable":
      return (
        <div className="max-w-3xl">
          <Callout tone="danger" title="Can't load your usage" headingLevel={1}>
            {usage.reason} Reload the page to try again.
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
            Choose a plan to see your usage.
          </Callout>
        </div>
      );
  }
}

function UsageOverview({ data }: { data: UsageRead }) {
  const local = useLocalResetTime(data.window.resets_at);
  // Only the AI-reply cap makes replies fall back; other meters show their own
  // "Platform limit" badge, and the concurrency cap only refuses new chats.
  const globalReached = data.meters.some(
    (m) => m.kind === "gemini_request" && m.global_limit_reached,
  );
  const globalBusy = data.concurrency.global_limit_reached;
  const empty = isEmptyDay(data);

  return (
    <div className="flex w-full max-w-[1280px] flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Eyebrow>Daily window</Eyebrow>
        <h1 className="text-h1 font-bold tracking-tight uppercase">Usage</h1>
        <p className="max-w-[72ch] text-base text-muted-foreground">
          Limits reset every day. Resets at{" "}
          <span className="font-mono">{utcResetText(data.window.resets_at)} UTC</span>
          {local && (
            <>
              , which is <span className="font-mono">{local}</span> in your local time
            </>
          )}
          .
        </p>
      </div>

      {globalReached && (
        <Callout tone="warning" title="Platform limit reached">
          {GLOBAL_LIMIT_MESSAGE}
        </Callout>
      )}

      {globalBusy && (
        <Callout tone="warning" title="Platform busy">
          {GLOBAL_CONCURRENCY_MESSAGE}
        </Callout>
      )}

      {empty && (
        <p className="max-w-[72ch] text-base text-muted-foreground">
          No usage yet today. Counts start at zero and rise as your receptionist handles
          conversations.
        </p>
      )}

      <section aria-label="Daily limits">
        <ul className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {data.meters.map((meter) => (
            <li key={meter.kind}>
              <MeterCard meter={meter} />
            </li>
          ))}
        </ul>
      </section>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle as="h2">Today so far</CardTitle>
            <CardDescription>Totals for the current daily window.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Stat label="AI tokens" value={data.gemini_tokens.toLocaleString("en-US")} />
              <Stat label="Failed requests" value={data.failed_requests.toLocaleString("en-US")} />
              <Stat
                label="Estimated cost"
                value={formatEstimatedCost(data.estimated_cost_usd)}
                note="Estimate only, not a bill."
              />
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h2">Conversations and calls</CardTitle>
            <CardDescription>
              An open conversation stops counting toward the limit after{" "}
              <span className="font-mono">{data.concurrency.idle_minutes}</span> idle minutes.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Stat
                label="Active conversations"
                value={`${data.concurrency.active} of ${data.concurrency.limit}`}
              />
              <Stat label="Max call length" value={formatDuration(data.max_call_duration_seconds)} />
            </dl>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} sec`;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-sm font-medium tracking-[0.02em] text-muted-foreground">{label}</dt>
      <dd className="font-mono text-h2 font-bold">{value}</dd>
      {note && <p className="text-sm text-muted-foreground">{note}</p>}
    </div>
  );
}

function MeterCard({ meter }: { meter: UsageMeter }) {
  const label = meterLabel(meter.kind);
  const atLimit = isAtLimit(meter);
  const percent = meter.limit > 0 ? Math.min(100, Math.round((meter.used / meter.limit) * 100)) : 100;

  return (
    <Card className="gap-3 p-6">
      <div className="flex items-start justify-between gap-3">
        <h2 id={`meter-${meter.kind}`} className="text-lg font-semibold">
          {label}
        </h2>
        {atLimit && (
          <Badge tone="danger">
            <CircleAlert aria-hidden />
            Limit reached
          </Badge>
        )}
        {!atLimit && meter.global_limit_reached && (
          <Badge tone="warning">
            <TriangleAlert aria-hidden />
            Platform limit
          </Badge>
        )}
      </div>
      <p className="font-mono text-h2 font-bold">
        {meter.used} <span className="text-muted-foreground">of</span> {meter.limit}
      </p>
      <div
        role="meter"
        aria-labelledby={`meter-${meter.kind}`}
        aria-valuemin={0}
        aria-valuemax={meter.limit}
        aria-valuenow={Math.min(meter.used, meter.limit)}
        aria-valuetext={`${meter.used} of ${meter.limit} used`}
        className="h-2 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div
          className={cn("h-full", atLimit ? "bg-danger" : "bg-foreground")}
          style={{ width: `${percent}%` }}
        />
      </div>
      {atLimit && (
        <p className="text-sm text-muted-foreground">
          Daily limit reached. This resets with the daily window.
        </p>
      )}
    </Card>
  );
}
