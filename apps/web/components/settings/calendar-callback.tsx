"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";

import { completeCalendarConnection } from "@/lib/api/client";
import { calendarMessage, googleErrorMessage } from "@/components/settings/calendar-messages";
import { buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Skeleton } from "@/components/ui/skeleton";

export const CALENDAR_PATH = "/dashboard/settings/calendar";
export const CALLBACK_PATH = `${CALENDAR_PATH}/callback`;

type State =
  | { kind: "connecting" }
  | { kind: "connected" }
  | { kind: "failed"; message: string };

/**
 * Where Google sends the owner back. The one-time `code` and `state` are read
 * once, removed from the address bar, and posted to the API exactly once: a
 * code is single-use, so a retry must start a new connection, never replay it.
 */
export function CalendarCallback({
  code,
  state,
  error,
}: {
  code: string | null;
  state: string | null;
  error: string | null;
}) {
  const { getToken } = useAuth();
  const router = useRouter();

  // Props change when the URL is stripped below; keep the first values.
  const [captured] = useState(() => ({ code, state, error }));
  // A ref, not state: React strict mode runs effects twice with the same refs.
  const started = useRef(false);

  const { error: googleError, code: oneTimeCode, state: oneTimeState } = captured;
  const incomplete = !googleError && (!oneTimeCode || !oneTimeState);
  const [view, setView] = useState<State>({ kind: "connecting" });

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const { error: err, code: c, state: s } = captured;
    // Nothing sensitive stays in the address bar or history.
    router.replace(CALLBACK_PATH);
    if (err || !c || !s) return;

    void (async () => {
      let result;
      try {
        const token = await getToken();
        if (!token) {
          router.replace("/sign-in");
          return;
        }
        result = await completeCalendarConnection(token, c, s);
      } catch {
        // The code may already be spent, so never retry it: start a new connection instead.
        setView({ kind: "failed", message: calendarMessage("unknown") });
        return;
      }
      if (result.kind === "ok") {
        setView({ kind: "connected" });
        router.replace(CALENDAR_PATH);
      } else if (result.kind === "unauthorized") {
        router.replace("/sign-in");
      } else {
        setView({
          kind: "failed",
          message: calendarMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined),
        });
      }
    })();
  }, [captured, getToken, router]);

  const tryAgain = (
    <Link href={CALENDAR_PATH} className={buttonVariants({ size: "sm" })}>
      Try again
    </Link>
  );

  if (googleError) {
    return (
      <div className="max-w-3xl">
        <Callout tone="warning" title="Calendar not connected" headingLevel={1} action={tryAgain}>
          {googleErrorMessage(googleError)}
        </Callout>
      </div>
    );
  }

  if (incomplete) {
    return (
      <div className="max-w-3xl">
        <Callout tone="danger" title="Calendar not connected" headingLevel={1} action={tryAgain}>
          This page needs the details Google sends after you approve access, and they are missing.
          Start the connection again.
        </Callout>
      </div>
    );
  }

  switch (view.kind) {
    case "connecting":
      return (
        <div
          role="status"
          aria-live="polite"
          className="flex w-full max-w-xl flex-col gap-4 rounded-md border border-border bg-card p-8 shadow-card"
        >
          <h1 className="text-h2 font-bold tracking-tight uppercase">Connecting your calendar</h1>
          <p className="text-base text-muted-foreground">Finishing the connection with Google.</p>
          <Skeleton className="h-2 w-full" />
        </div>
      );
    case "connected":
      return (
        <div role="status" aria-live="polite" className="max-w-3xl">
          <Callout tone="success" title="Google account connected" headingLevel={1}>
            Taking you back to choose a calendar.
          </Callout>
        </div>
      );
    case "failed":
      return (
        <div className="max-w-3xl">
          <Callout tone="danger" title="Calendar not connected" headingLevel={1} action={tryAgain}>
            {view.message}
          </Callout>
        </div>
      );
  }
}
