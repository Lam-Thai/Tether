import type { ReactNode } from "react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { cn } from "@/lib/utils";

export const SESSION_EXPIRED = "Your session has expired. Please sign in again.";

export const PLAN_REQUIRED = (
  <>
    Your plan is not active.{" "}
    <Link href="/billing/plan" className="font-medium underline">
      Choose a plan
    </Link>
  </>
);

/** Messages for the failure kinds every builder action shares. */
export function commonFailure(kind: string, reason?: string): ReactNode {
  if (kind === "unauthorized") return SESSION_EXPIRED;
  if (kind === "subscription_required") return PLAN_REQUIRED;
  return reason ?? "Something went wrong. Please try again.";
}

/**
 * Callout for a builder section whose GET did not return data. The page
 * redirects on 401/402 first; these cases are the backstop, so a failing
 * section never breaks the rest of the page.
 */
export function SectionLoadFailure({
  kind,
  reason,
  noun,
}: {
  kind: "unauthorized" | "subscription_required" | "unreachable" | "not_found";
  reason?: string;
  /** Plural noun used in copy, e.g. "services". */
  noun: string;
}) {
  switch (kind) {
    case "unauthorized":
      return (
        <Callout tone="danger" title="Signed out" headingLevel={2}>
          Your session has expired. <Link href="/sign-in" className="underline">Sign in again</Link>.
        </Callout>
      );
    case "subscription_required":
      return (
        <Callout
          tone="warning"
          title="Plan required"
          headingLevel={2}
          action={
            <Link href="/billing/plan" className={buttonVariants({ size: "sm" })}>
              Choose plan
            </Link>
          }
        >
          Choose a plan to manage {noun}.
        </Callout>
      );
    case "not_found":
      return (
        <Callout tone="warning" title="Business needed" headingLevel={2}>
          Set up your business before managing {noun}.
        </Callout>
      );
    case "unreachable":
      return (
        <Callout tone="danger" title={`Can't load your ${noun}`} headingLevel={2}>
          {reason} Reload the page to try again.
        </Callout>
      );
  }
}

/** On/off switch (`role="switch"`) that disables itself while its request is in flight. */
export function Switch({
  checked,
  pending,
  label,
  onToggle,
  onLabel = "On",
  offLabel = "Off",
}: {
  checked: boolean;
  pending: boolean;
  label: string;
  onToggle: () => void;
  onLabel?: string;
  offLabel?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-busy={pending || undefined}
      disabled={pending}
      onClick={onToggle}
      className="inline-flex h-11 items-center gap-2 rounded-md px-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-wait"
    >
      <span
        aria-hidden
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-input transition-colors duration-200 motion-reduce:transition-none",
          checked ? "bg-foreground" : "bg-muted",
          pending && "opacity-60",
        )}
      >
        <span
          className={cn(
            "inline-block size-4 rounded-full transition-transform duration-200 motion-reduce:transition-none",
            checked ? "translate-x-6 bg-card" : "translate-x-1 bg-foreground",
          )}
        />
      </span>
      <span aria-hidden className="min-w-7 text-sm font-medium">
        {checked ? onLabel : offLabel}
      </span>
    </button>
  );
}
