"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";

import { syncCheckout } from "@/lib/api/client";
import { Button, buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Skeleton } from "@/components/ui/skeleton";

// ~30s in total: Stripe usually confirms within a few seconds.
export const POLL_DELAYS_MS = [1000, 2000, 3000, 5000, 8000, 11000];

type State =
  | { kind: "confirming" }
  | { kind: "active" }
  | { kind: "still_pending" }
  | { kind: "not_found" }
  | { kind: "error"; reason: string };

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Lands here from Stripe Checkout. The `session_id` in the URL proves
 * nothing on its own: this only says "active" once the API, having asked
 * Stripe itself, reports the plan as entitled.
 */
export function CheckoutReturn({ sessionId }: { sessionId: string | null }) {
  const { getToken } = useAuth();
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "confirming" });
  const [attempt, setAttempt] = useState(0);

  // The polling effect must only restart on a new session or an explicit
  // retry, never because a hook handed back a new function identity.
  const latest = useRef({ getToken, router });
  useEffect(() => {
    latest.current = { getToken, router };
  });

  const confirm = useCallback(
    async (isCancelled: () => boolean) => {
      if (!sessionId) return;
      const { getToken, router } = latest.current;
      const token = await getToken();
      if (isCancelled()) return;
      if (!token) {
        router.replace("/sign-in");
        return;
      }

      // Sync is idempotent and asks Stripe directly, so retrying it (rather
      // than waiting for the webhook) also recovers from a transient failure.
      let lastError: string | null = null;
      for (const delay of [0, ...POLL_DELAYS_MS]) {
        if (delay) await wait(delay);
        if (isCancelled()) return;
        const fresh = delay ? await getToken() : token;
        if (isCancelled()) return;
        if (!fresh) {
          router.replace("/sign-in");
          return;
        }
        const result = await syncCheckout(fresh, sessionId);
        if (isCancelled()) return;
        if (result.kind === "not_found") {
          setState({ kind: "not_found" });
          return;
        }
        if (result.kind === "unauthorized") {
          router.replace("/sign-in");
          return;
        }
        if (result.kind === "ok" && result.data.entitled) {
          setState({ kind: "active" });
          router.replace("/dashboard");
          return;
        }
        lastError = result.kind === "unreachable" ? result.reason : null;
      }
      setState(lastError ? { kind: "error", reason: lastError } : { kind: "still_pending" });
    },
    [sessionId],
  );

  useEffect(() => {
    let cancelled = false;
    void confirm(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [confirm, attempt]);

  const retry = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => {
        setState({ kind: "confirming" });
        setAttempt((n) => n + 1);
      }}
    >
      Check again
    </Button>
  );

  // No session id means there is nothing to confirm: derived, not fetched.
  const view: State = sessionId ? state : { kind: "not_found" };

  switch (view.kind) {
    case "confirming":
      return (
        <div
          role="status"
          aria-live="polite"
          className="flex w-full max-w-xl flex-col gap-4 rounded-md border border-border bg-card p-8 shadow-card"
        >
          <h1 className="text-h2 font-bold tracking-tight uppercase">Confirming your plan</h1>
          <p className="text-base text-muted-foreground">
            Checking with Stripe. This usually takes a few seconds.
          </p>
          <Skeleton className="h-2 w-full" />
        </div>
      );
    case "active":
      return (
        <div role="status" aria-live="polite" className="w-full max-w-xl">
          <Callout tone="success" title="You're all set" headingLevel={1}>
            Taking you to your dashboard.
          </Callout>
        </div>
      );
    case "still_pending":
      return (
        <div className="w-full max-w-xl">
          <Callout tone="warning" title="Still confirming" headingLevel={1} action={retry}>
            Stripe hasn&apos;t confirmed your plan yet. Check again in a minute; you won&apos;t be
            charged twice.
          </Callout>
        </div>
      );
    case "not_found":
      return (
        <div className="w-full max-w-xl">
          <Callout
            tone="danger"
            title="We couldn't find that checkout"
            headingLevel={1}
            action={
              <Link href="/billing/plan" className={buttonVariants({ size: "sm" })}>
                Choose a plan
              </Link>
            }
          >
            The link may be incomplete or from a different account.
          </Callout>
        </div>
      );
    case "error":
      return (
        <div className="w-full max-w-xl">
          <Callout tone="danger" title="Can't confirm your plan" headingLevel={1} action={retry}>
            {view.reason}
          </Callout>
        </div>
      );
  }
}
