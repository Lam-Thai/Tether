"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CirclePause,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import type {
  IntegrationCalendar,
  IntegrationPhone,
  IntegrationStatus,
  IntegrationStatusResult,
  UsageRead,
} from "@/lib/api/client";
import { formatBusinessTime } from "@/components/inbox/labels";
import { formatPhoneNumber } from "@/components/settings/phone-messages";
import { meterLabel } from "@/components/settings/usage-panel";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  calendarStatusView,
  describeIntegrationError,
  phoneStatusView,
  type StatusTone,
} from "./integration-labels";

type HeadingLevel = "h2" | "h3";

const TONE_ICON: Record<StatusTone, LucideIcon> = {
  success: CircleCheck,
  warning: TriangleAlert,
  neutral: CircleDashed,
};

function StatusBadge({ tone, label, icon }: { tone: StatusTone; label: string; icon?: LucideIcon }) {
  const Icon = icon ?? TONE_ICON[tone];
  return (
    <Badge tone={tone}>
      <Icon aria-hidden />
      {label}
    </Badge>
  );
}

/** A failed fetch for one section: names what could not load, never blocks the rest of the page. */
export function SectionFailure({
  result,
  title,
  planNote,
}: {
  result: { kind: "unauthorized" } | { kind: "subscription_required" } | { kind: "unreachable"; reason: string };
  title: string;
  planNote: string;
}) {
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
          {planNote}
        </Callout>
      );
    case "unreachable":
      return (
        <Callout tone="danger" title={title}>
          {result.reason} Reload the page to try again.
        </Callout>
      );
  }
}

/**
 * Calendar, phone, agent and usage cards plus the recent integration errors,
 * all from one `GET /integrations/status` result. `detailed` adds the extra
 * rows the integrations page shows.
 */
export function IntegrationSection({
  result,
  timeZone,
  detailed = false,
  headingLevel = "h3",
}: {
  result: IntegrationStatusResult;
  timeZone: string;
  detailed?: boolean;
  /** Level of each card's heading; the page owns the levels above. */
  headingLevel?: HeadingLevel;
}) {
  if (result.kind !== "ok") {
    return (
      <SectionFailure
        result={result}
        title="Can't load integration status"
        planNote="Choose a plan to see your integrations."
      />
    );
  }
  const { data } = result;

  return (
    <div className="flex flex-col gap-6">
      <ul aria-label="Integration status" className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <li>
          <CalendarCard calendar={data.calendar} timeZone={timeZone} detailed={detailed} heading={headingLevel} />
        </li>
        <li>
          <PhoneCard phone={data.phone} timeZone={timeZone} detailed={detailed} heading={headingLevel} />
        </li>
        <li>
          <AgentCard agent={data.agent} heading={headingLevel} />
        </li>
        <li>
          <UsageCard usage={data.usage} heading={headingLevel} />
        </li>
      </ul>
      <IntegrationErrors errors={data.errors} timeZone={timeZone} heading={headingLevel} />
    </div>
  );
}

function CardHead({
  title,
  heading,
  badge,
}: {
  title: string;
  heading: HeadingLevel;
  badge?: ReactNode;
}) {
  const Heading = heading;
  return (
    <div className="flex items-start justify-between gap-3">
      <Heading className="text-lg font-semibold">{title}</Heading>
      {badge}
    </div>
  );
}

function ActionLink({ href, children, primary = false }: { href: string; children: ReactNode; primary?: boolean }) {
  return (
    <Link href={href} className={cn(buttonVariants({ variant: primary ? "default" : "outline", size: "sm" }), "w-fit")}>
      {children}
    </Link>
  );
}

function CalendarCard({
  calendar,
  timeZone,
  detailed,
  heading,
}: {
  calendar: IntegrationCalendar;
  timeZone: string;
  detailed: boolean;
  heading: HeadingLevel;
}) {
  const view = calendarStatusView(calendar.status, calendar.configured);
  let body: ReactNode;
  let action: ReactNode;

  if (!calendar.configured && calendar.status === "not_connected") {
    body = "Google Calendar isn't set up on this server yet, so bookings can't be checked or saved.";
  } else if (calendar.status === "connected") {
    body = calendar.calendar_name ? (
      <>
        Bookings go into <span className="font-mono">{calendar.calendar_name}</span>.
      </>
    ) : (
      "Bookings go into your Google Calendar."
    );
    action = <ActionLink href="/dashboard/settings/calendar">Manage calendar</ActionLink>;
  } else if (calendar.status === "reconnect_needed") {
    body = "Google stopped accepting Tether's access, so it can't check or book your calendar.";
    action = (
      <ActionLink href="/dashboard/settings/calendar" primary>
        Reconnect calendar
      </ActionLink>
    );
  } else if (calendar.status === "needs_selection") {
    body = "Google Calendar is connected. Choose which calendar bookings go into.";
    action = (
      <ActionLink href="/dashboard/settings/calendar" primary>
        Choose calendar
      </ActionLink>
    );
  } else if (calendar.status === "not_connected") {
    body = "Connect Google Calendar so bookings land on your calendar.";
    action = (
      <ActionLink href="/dashboard/settings/calendar" primary>
        Connect calendar
      </ActionLink>
    );
  } else {
    body = "Open the calendar page for the current state.";
    action = <ActionLink href="/dashboard/settings/calendar">Open calendar</ActionLink>;
  }

  return (
    <Card className="h-full gap-3 p-6">
      <CardHead
        title="Calendar"
        heading={heading}
        badge={<StatusBadge tone={view.tone} label={view.label} />}
      />
      <p className="text-base text-muted-foreground">{body}</p>
      {detailed && calendar.last_synced_at && (
        <p className="text-sm text-muted-foreground">
          Last checked <time dateTime={calendar.last_synced_at} className="font-mono">{formatBusinessTime(calendar.last_synced_at, timeZone)}</time>
        </p>
      )}
      {action && <div className="mt-auto pt-1">{action}</div>}
    </Card>
  );
}

function PhoneCard({
  phone,
  timeZone,
  detailed,
  heading,
}: {
  phone: IntegrationPhone;
  timeZone: string;
  detailed: boolean;
  heading: HeadingLevel;
}) {
  const view = phoneStatusView(phone.status, phone.configured);
  let body: ReactNode;
  let action: ReactNode;

  if (!phone.configured && phone.status === "not_connected") {
    body = "Phone calls aren't set up on this server yet. The test chat still works.";
  } else if (phone.status === "connected") {
    body = (
      <>
        Calls to{" "}
        <span className="font-mono">{phone.phone_number ? formatPhoneNumber(phone.phone_number) : "your number"}</span>{" "}
        reach your receptionist.
      </>
    );
    action = <ActionLink href="/dashboard/phone">Manage phone</ActionLink>;
  } else if (phone.status === "misconfigured") {
    body = (
      <>
        {phone.phone_number && <span className="font-mono">{formatPhoneNumber(phone.phone_number)}</span>}
        {phone.phone_number ? ": " : ""}
        Your phone number needs attention. Test it on the Phone page.
      </>
    );
    action = (
      <ActionLink href="/dashboard/phone" primary>
        Open phone
      </ActionLink>
    );
  } else if (phone.status === "not_connected") {
    body = "No phone number is connected, so callers can't reach your receptionist.";
    action = (
      <ActionLink href="/dashboard/phone" primary>
        Connect a number
      </ActionLink>
    );
  } else {
    body = "Open the phone page for the current state.";
    action = <ActionLink href="/dashboard/phone">Open phone</ActionLink>;
  }

  return (
    <Card className="h-full gap-3 p-6">
      <CardHead title="Phone" heading={heading} badge={<StatusBadge tone={view.tone} label={view.label} />} />
      <p className="text-base text-muted-foreground">{body}</p>
      {detailed && phone.status === "connected" && phone.verified_at && (
        <p className="text-sm text-muted-foreground">
          Webhooks verified <time dateTime={phone.verified_at} className="font-mono">{formatBusinessTime(phone.verified_at, timeZone)}</time>
        </p>
      )}
      {action && <div className="mt-auto pt-1">{action}</div>}
    </Card>
  );
}

function AgentCard({
  agent,
  heading,
}: {
  agent: { active: boolean; ready: boolean };
  heading: HeadingLevel;
}) {
  const badge = agent.active ? (
    <StatusBadge tone="success" label="Active" />
  ) : (
    <StatusBadge tone="warning" label="Inactive" icon={CirclePause} />
  );
  let body: string;
  if (agent.active) body = "Your receptionist is answering callers.";
  else if (agent.ready) body = "Setup is complete. Callers hear that you're not available until you activate.";
  else body = "Finish the setup steps, then activate. Until then callers hear that you're not available.";

  return (
    <Card className="h-full gap-3 p-6">
      <CardHead title="Receptionist" heading={heading} badge={badge} />
      <p className="text-base text-muted-foreground">{body}</p>
      <div className="mt-auto pt-1">
        <ActionLink href="/dashboard/agent#activation" primary={!agent.active && agent.ready}>
          {agent.active ? "Manage activation" : agent.ready ? "Activate" : "See checklist"}
        </ActionLink>
      </div>
    </Card>
  );
}

function UsageCard({ usage, heading }: { usage: UsageRead; heading: HeadingLevel }) {
  const anyAtLimit = usage.meters.some((m) => m.used >= m.limit);
  const anyGlobal = usage.meters.some((m) => m.global_limit_reached);

  return (
    <Card className="h-full gap-3 p-6">
      <CardHead
        title="Usage today"
        heading={heading}
        badge={
          anyAtLimit ? (
            <StatusBadge tone="warning" label="Limit reached" icon={CircleAlert} />
          ) : anyGlobal ? (
            <StatusBadge tone="warning" label="Platform limit" />
          ) : undefined
        }
      />
      {usage.meters.length === 0 ? (
        <p className="text-base text-muted-foreground">No usage limits are reported for today.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {usage.meters.map((meter) => {
            const label = meterLabel(meter.kind);
            const atLimit = meter.used >= meter.limit;
            const percent = meter.limit > 0 ? Math.min(100, Math.round((meter.used / meter.limit) * 100)) : 100;
            return (
              <li key={meter.kind} className="flex flex-col gap-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium">{label}</span>
                  <span className="font-mono text-sm">
                    {meter.used} <span className="text-muted-foreground">of</span> {meter.limit}
                  </span>
                </div>
                <div
                  role="meter"
                  aria-label={label}
                  aria-valuemin={0}
                  aria-valuemax={meter.limit}
                  aria-valuenow={Math.min(meter.used, meter.limit)}
                  aria-valuetext={`${meter.used} of ${meter.limit} used${atLimit ? ", limit reached" : ""}`}
                  className="h-2 w-full overflow-hidden rounded-sm bg-muted"
                >
                  <div className={cn("h-full", atLimit ? "bg-danger" : "bg-foreground")} style={{ width: `${percent}%` }} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-auto pt-1">
        <ActionLink href="/dashboard/settings/usage">Usage details</ActionLink>
      </div>
    </Card>
  );
}

/** Recent integration errors with copy and a fix link. Codes are fixed strings from the API. */
export function IntegrationErrors({
  errors,
  timeZone,
  heading,
}: {
  errors: IntegrationStatus["errors"];
  timeZone: string;
  heading: HeadingLevel;
}) {
  const Heading = heading;
  return (
    <Card className="gap-4 p-6">
      <Heading className="text-lg font-semibold">Recent integration errors</Heading>
      {errors.length === 0 ? (
        <p className="flex items-center gap-2 text-base text-muted-foreground">
          <CircleCheck aria-hidden className="size-5 shrink-0 text-foreground" />
          No integration errors.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {errors.map((error, i) => {
            const view = describeIntegrationError(error);
            return (
              <li
                key={`${error.source}-${error.code}-${i}`}
                className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0 sm:grid-cols-[auto_minmax(0,1fr)_auto]"
              >
                <TriangleAlert aria-hidden className="mt-0.5 size-5 shrink-0 text-danger-foreground" />
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-base font-semibold">{view.source}</span>
                  <span className="text-base">{view.message}</span>
                  <span className="text-sm text-muted-foreground">
                    <span className="font-mono">{view.code}</span>
                    {error.occurred_at && (
                      <>
                        {" · "}
                        <time dateTime={error.occurred_at}>{formatBusinessTime(error.occurred_at, timeZone)}</time>
                      </>
                    )}
                  </span>
                </div>
                <Link
                  href={view.fixUrl}
                  aria-label={`Fix ${view.source} problem`}
                  className={cn(buttonVariants({ variant: "outline", size: "sm" }), "col-start-2 w-fit sm:col-start-auto")}
                >
                  Fix
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
