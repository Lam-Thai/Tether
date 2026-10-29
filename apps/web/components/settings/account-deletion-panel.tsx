"use client";

import { useId, useRef, useState } from "react";
import { useAuth, useClerk } from "@clerk/nextjs";
import { Check, LoaderCircle, Trash2 } from "lucide-react";

import { deleteAccount } from "@/lib/api/client";
import { Dialog } from "@/components/agent/faq-dialog";
import { SESSION_EXPIRED } from "@/components/agent/section-shared";
import { ExternalLink } from "@/components/legal/prose";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** The phrase the API compares against (after trimming). The API owns the decision. */
export const ACCOUNT_CONFIRMATION_PHRASE = "DELETE MY ACCOUNT";

// In the order the API does them. Stripe goes first: if it fails, nothing else is touched.
const DELETED = [
  "Your Stripe customer record. Your subscription ends now, with no refund.",
  "Upcoming appointments Tether added to your Google Calendar are cancelled. Tether tells you about any it couldn't cancel.",
  "Your Google Calendar access is revoked and the stored token is deleted.",
  "Your business and everything in it: setup, services, FAQs, hours, conversations, bookings and usage logs.",
  "Your sign-in. You are signed out.",
] as const;

const KEPT = [
  "Past events on your Google Calendar.",
  "Billing records that Stripe keeps under its own obligations.",
  "Usage totals that are not tied to any business.",
] as const;

const TRY_AGAIN_LATER = "We couldn't end your subscription with Stripe, so nothing was deleted. Try again in a few minutes.";

type Failure = { kind: "mismatch" } | { kind: "message"; message: React.ReactNode };

/** What the API reported, merged across retries (a retry only sees what is left). */
interface Outcome {
  notCancelled: number;
  /** A calendar connection existed and Google did not confirm the revoke. */
  revokeUnconfirmed: boolean;
  signInDeleted: boolean;
}

/**
 * Self-serve account deletion behind a typed confirmation. Nothing is reported as
 * deleted until the API returns its 200. `card` is the Settings > Data danger zone;
 * `disclosure` is the low-emphasis version for owners who have no plan yet.
 */
export function AccountDeletionPanel({ variant = "card" }: { variant?: "card" | "disclosure" }) {
  const { getToken } = useAuth();
  const { signOut } = useClerk();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  // A ref as well as state: two quick submits must not both pass the `pending` check.
  const inFlight = useRef(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [leaving, setLeaving] = useState(false);

  async function leave() {
    setLeaving(true);
    try {
      await signOut({ redirectUrl: "/" });
    } catch {
      // The session is already gone with the user; go home either way.
      window.location.assign("/");
    }
  }

  async function submit(phrase: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setFailure(null);

    try {
      const token = await getToken();
      if (!token) {
        setFailure({ kind: "message", message: SESSION_EXPIRED });
        if (!outcome) setOpen(true);
        return;
      }

      const result = await deleteAccount(token, phrase);
      switch (result.kind) {
        case "ok": {
          // Only the API's own report is shown, and only now.
          const merged: Outcome = {
            notCancelled: (outcome?.notCancelled ?? 0) + result.data.calendar_events_not_cancelled,
            // A retry finds no connection (`null`), so an earlier warning must survive it.
            revokeUnconfirmed:
              (outcome?.revokeUnconfirmed ?? false) || result.data.calendar_token_revoked === false,
            signInDeleted: result.data.sign_in_deleted,
          };
          setOutcome(merged);
          setOpen(false);
          // Hold the page when the owner has something to do by hand; otherwise just leave.
          if (merged.signInDeleted && merged.notCancelled === 0 && !merged.revokeUnconfirmed) void leave();
          return;
        }
        case "confirmation_mismatch":
          setFailure({ kind: "mismatch" });
          break;
        case "billing_unreachable":
          setFailure({ kind: "message", message: TRY_AGAIN_LATER });
          break;
        case "unauthorized":
          setFailure({ kind: "message", message: SESSION_EXPIRED });
          break;
        case "unreachable":
          setFailure({
            kind: "message",
            message:
              "Couldn't confirm whether your account was deleted. Reload this page: if you are still signed in, try again.",
          });
          break;
      }
      // The dialog may have been closed while waiting; the failure belongs in it.
      if (!outcome) setOpen(true);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  if (outcome) {
    return (
      <DeletionOutcome
        outcome={outcome}
        pending={pending}
        leaving={leaving}
        failure={failure}
        onRetry={() => submit(ACCOUNT_CONFIRMATION_PHRASE)}
        onLeave={leave}
      />
    );
  }

  const openDialog = () => {
    setFailure(null);
    setOpen(true);
  };

  return (
    <>
      {variant === "card" ? (
        <Card className="max-w-3xl border-l-[6px] border-l-danger">
          <CardHeader>
            <CardTitle as="h2" className="flex items-center gap-3">
              <Trash2 aria-hidden className="size-6 shrink-0 text-danger-foreground" />
              Delete account
            </CardTitle>
            <CardDescription>
              Permanently deletes your business, everything in it and your sign-in. This cannot be undone.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button type="button" variant="destructive" onClick={openDialog}>
              <Trash2 aria-hidden />
              Delete account
            </Button>
          </CardContent>
        </Card>
      ) : (
        <details className="w-full max-w-4xl text-sm">
          <summary className="w-fit cursor-pointer text-muted-foreground underline underline-offset-4 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            Delete account
          </summary>
          <div className="mt-3 flex max-w-xl flex-col items-start gap-3 rounded-md border border-l-[6px] border-border border-l-danger bg-card p-4">
            <p className="text-muted-foreground">
              Done with Tether? This deletes your business, its data and your sign-in. This cannot be
              undone.
            </p>
            <Button type="button" variant="destructive" size="sm" onClick={openDialog}>
              <Trash2 aria-hidden />
              Delete account
            </Button>
          </div>
        </details>
      )}

      <Dialog open={open} onClose={() => setOpen(false)} title="Delete account?">
        <ConfirmForm pending={pending} failure={failure} onConfirm={submit} onCancel={() => setOpen(false)} />
      </Dialog>
    </>
  );
}

function ConfirmForm({
  pending,
  failure,
  onConfirm,
  onCancel,
}: {
  pending: boolean;
  failure: Failure | null;
  onConfirm: (phrase: string) => void;
  onCancel: () => void;
}) {
  const uid = useId();
  const [typed, setTyped] = useState("");
  // The same trim the API applies; the API still makes the final call.
  const matches = typed.trim() === ACCOUNT_CONFIRMATION_PHRASE;

  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (matches && !pending) onConfirm(typed);
      }}
    >
      <div className="flex flex-col gap-4">
        <section aria-labelledby={`${uid}-deleted`} className="flex flex-col gap-2">
          <h3 id={`${uid}-deleted`} className="text-base font-bold uppercase">
            Deleted
          </h3>
          <ol className="flex flex-col gap-1.5 text-sm">
            {DELETED.map((line) => (
              <li key={line} className="flex items-start gap-2">
                <Trash2 aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-foreground" />
                {line}
              </li>
            ))}
          </ol>
        </section>
        <section aria-labelledby={`${uid}-kept`} className="flex flex-col gap-2">
          <h3 id={`${uid}-kept`} className="text-base font-bold uppercase">
            Kept
          </h3>
          <ul className="flex flex-col gap-1.5 text-sm">
            {KEPT.map((line) => (
              <li key={line} className="flex items-start gap-2">
                <Check aria-hidden className="mt-0.5 size-4 shrink-0" />
                {line}
              </li>
            ))}
          </ul>
        </section>
      </div>

      {failure && (
        <FormError>
          {failure.kind === "mismatch"
            ? "That phrase didn't match, so nothing was deleted. Type it exactly as shown."
            : failure.message}
        </FormError>
      )}

      <div className="flex flex-col gap-2">
        <Label htmlFor={`${uid}-confirm`}>
          Type <span className="font-mono">{ACCOUNT_CONFIRMATION_PHRASE}</span> to confirm
        </Label>
        <Input
          id={`${uid}-confirm`}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          disabled={pending}
          className="font-mono"
        />
      </div>

      <div className="flex flex-wrap justify-end gap-3">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="destructive" disabled={!matches || pending} aria-busy={pending || undefined}>
          {pending ? <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" /> : <Trash2 aria-hidden />}
          {pending ? "Deleting…" : "Delete account"}
        </Button>
      </div>
    </form>
  );
}

/** What is left to do by hand after the API's 200. */
function Followups({ outcome }: { outcome: Outcome }) {
  const { notCancelled, revokeUnconfirmed } = outcome;
  if (notCancelled === 0 && !revokeUnconfirmed) return null;

  return (
    <ul className="flex flex-col gap-1.5">
      {notCancelled > 0 && (
        <li>
          {notCancelled === 1
            ? "1 upcoming event could not be cancelled. Remove it in Google Calendar yourself."
            : `${notCancelled} upcoming events could not be cancelled. Remove them in Google Calendar yourself.`}
        </li>
      )}
      {revokeUnconfirmed && (
        <li>
          Google did not confirm that Tether&apos;s calendar access was revoked. Remove it at{" "}
          <ExternalLink href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</ExternalLink>.
        </li>
      )}
    </ul>
  );
}

function DeletionOutcome({
  outcome,
  pending,
  leaving,
  failure,
  onRetry,
  onLeave,
}: {
  outcome: Outcome;
  pending: boolean;
  leaving: boolean;
  failure: Failure | null;
  onRetry: () => void;
  onLeave: () => void;
}) {
  const followups = <Followups outcome={outcome} />;

  if (!outcome.signInDeleted) {
    return (
      <div className="flex max-w-3xl flex-col gap-4">
        <Callout
          tone="warning"
          title="Data deleted, sign-in not removed"
          headingLevel={2}
          action={
            <div className="flex flex-wrap gap-3">
              <Button
                type="button"
                variant="outline"
                onClick={onRetry}
                disabled={pending || leaving}
                aria-busy={pending || undefined}
              >
                {pending && <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" />}
                {pending ? "Trying again…" : "Try again"}
              </Button>
              <Button type="button" variant="outline" onClick={onLeave} disabled={pending || leaving}>
                Sign out
              </Button>
            </div>
          }
        >
          <div className="flex flex-col gap-2">
            <p>
              Your business and its data are deleted. We couldn&apos;t remove your sign-in. Try again to finish.
            </p>
            {followups}
          </div>
        </Callout>
        {failure && <FormError>{failure.kind === "mismatch" ? "That didn't work. Try again." : failure.message}</FormError>}
      </div>
    );
  }

  const holds = outcome.notCancelled > 0 || outcome.revokeUnconfirmed;
  return (
    <Callout
      tone="success"
      title="Account deleted"
      headingLevel={2}
      className="max-w-3xl"
      action={
        holds ? (
          <Button type="button" variant="outline" onClick={onLeave} disabled={leaving}>
            Sign out
          </Button>
        ) : undefined
      }
    >
      {holds ? <div className="flex flex-col gap-2">{followups}</div> : "Signing you out…"}
    </Callout>
  );
}
