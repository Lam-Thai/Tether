"use client";

import { useId, useRef, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { Check, LoaderCircle, Trash2, TriangleAlert } from "lucide-react";

import { deleteTestData, type TestDataReport } from "@/lib/api/client";
import { Dialog } from "@/components/agent/faq-dialog";
import { PLAN_REQUIRED, SESSION_EXPIRED } from "@/components/agent/section-shared";
import { SettingsNav } from "@/components/settings/settings-nav";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** The phrase the API compares against (after trimming). The API owns the decision. */
export const CONFIRMATION_PHRASE = "DELETE TEST DATA";

const DELETED = [
  "All conversations and their messages, from test chats and calls.",
  "Bookings, and the escalations and text-alert records attached to conversations.",
  "Usage history (today's estimated cost starts again from zero), and generated reply audio.",
  "Google Calendar events for confirmed bookings are cancelled.",
] as const;

const KEPT = [
  "Your business details, receptionist settings, services, FAQs and opening hours.",
  "The escalation contact, calendar and phone connections, and your plan.",
  "Today's usage counts, so daily limits are unchanged.",
] as const;

type Failure = { kind: "mismatch" } | { kind: "message"; message: React.ReactNode };

/**
 * `/dashboard/settings/data`: the one destructive action, behind a typed
 * confirmation. Nothing is reported as deleted until the API returns its counts.
 */
export function TestDataPanel() {
  const { getToken } = useAuth();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  // A ref as well as state: two quick submits must not both pass the `pending` check.
  const inFlight = useRef(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [report, setReport] = useState<TestDataReport | null>(null);

  async function confirm(phrase: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setFailure(null);

    try {
      const token = await getToken();
      if (!token) {
        setFailure({ kind: "message", message: SESSION_EXPIRED });
        setOpen(true);
        return;
      }

      const result = await deleteTestData(token, phrase);
      switch (result.kind) {
        case "ok":
          // Only the API's own counts are shown, and only now.
          setReport(result.data);
          setOpen(false);
          return;
        case "confirmation_mismatch":
          setFailure({ kind: "mismatch" });
          break;
        case "unauthorized":
          setFailure({ kind: "message", message: SESSION_EXPIRED });
          break;
        case "subscription_required":
          setFailure({ kind: "message", message: PLAN_REQUIRED });
          break;
        case "unreachable":
          setFailure({
            kind: "message",
            message:
              "Couldn't confirm that the data was deleted. Open the inbox to check before you try again.",
          });
          break;
      }
      // The dialog may have been closed while waiting; the failure belongs in it.
      setOpen(true);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  return (
    <div className="flex w-full max-w-[1280px] flex-col gap-6">
      <SettingsNav current="data" />
      <div className="flex flex-col gap-2">
        <h1 className="text-h1 font-bold tracking-tight uppercase">Data</h1>
        <p className="max-w-[72ch] text-base text-muted-foreground">
          Clear out the conversations and bookings you made while trying Tether, without touching how
          your receptionist is set up.
        </p>
      </div>

      {report && <DeletionResult report={report} />}

      <Card className="max-w-3xl border-l-[6px] border-l-danger">
        <CardHeader>
          <CardTitle as="h2" className="flex items-center gap-3">
            <Trash2 aria-hidden className="size-6 shrink-0 text-danger-foreground" />
            Delete test data
          </CardTitle>
          <CardDescription>
            Removes conversations, bookings and usage history for this business. This cannot be
            undone.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            type="button"
            variant="destructive"
            onClick={() => {
              setFailure(null);
              setOpen(true);
            }}
          >
            <Trash2 aria-hidden />
            Delete test data
          </Button>
        </CardContent>
      </Card>

      <Dialog open={open} onClose={() => setOpen(false)} title="Delete test data?">
        <ConfirmForm pending={pending} failure={failure} onConfirm={confirm} onCancel={() => setOpen(false)} />
      </Dialog>
    </div>
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
  const matches = typed.trim() === CONFIRMATION_PHRASE;

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
          <ul className="flex flex-col gap-1.5 text-sm">
            {DELETED.map((line) => (
              <li key={line} className="flex items-start gap-2">
                <Trash2 aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-foreground" />
                {line}
              </li>
            ))}
          </ul>
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
          Type <span className="font-mono">{CONFIRMATION_PHRASE}</span> to confirm
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
          {pending ? "Deleting…" : "Delete test data"}
        </Button>
      </div>
    </form>
  );
}

function DeletionResult({ report }: { report: TestDataReport }) {
  const rows: [string, number][] = [
    ["Conversations", report.interactions],
    ["Messages", report.messages],
    ["Bookings", report.bookings],
    ["Escalations", report.escalations],
    ["Usage events", report.usage_events],
    ["Calendar events cancelled", report.calendar_events_cancelled],
  ];
  const stuck = report.calendar_events_not_cancelled;

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <Callout tone="success" title="Test data deleted" headingLevel={2}>
        <dl aria-label="Deleted counts" className="grid grid-cols-2 gap-x-6 gap-y-3 pt-1 sm:grid-cols-3">
          {rows.map(([label, value]) => (
            <div key={label} className="flex flex-col gap-0.5">
              <dt className="text-sm">{label}</dt>
              <dd className="font-mono text-h2 font-bold text-foreground">{value.toLocaleString("en-US")}</dd>
            </div>
          ))}
        </dl>
      </Callout>
      {stuck > 0 && (
        <Callout tone="warning" title="Some calendar events remain" headingLevel={2}>
          <span className="flex items-start gap-2">
            <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
            <span>
              {stuck === 1
                ? "1 calendar event could not be cancelled."
                : `${stuck} calendar events could not be cancelled.`}{" "}
              Remove {stuck === 1 ? "it" : "them"} in Google Calendar.
            </span>
          </span>
        </Callout>
      )}
    </div>
  );
}
