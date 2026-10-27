"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { CircleCheck, PhoneCall, RefreshCw, TriangleAlert, Unplug } from "lucide-react";

import {
  associatePhoneNumber,
  disconnectPhoneNumber,
  testPhoneNumber,
  type AvailableNumber,
  type PhoneNumber,
  type PhoneNumbers,
  type PhoneNumbersResult,
} from "@/lib/api/client";
import { Dialog } from "@/components/agent/faq-dialog";
import {
  formatPhoneNumber,
  numberErrorMessage,
  phoneMessage,
  SESSION_EXPIRED,
  TWILIO_CONSOLE_URL,
} from "@/components/settings/phone-messages";
import { Button, buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Eyebrow } from "@/components/ui/eyebrow";
import { FieldError, FormError } from "@/components/ui/form-error";

const SIGN_IN_PATH = "/sign-in";

/**
 * Presentational view of `/dashboard/phone`: switches exhaustively on the
 * `GET /phone-numbers` result. Loading lives in the route's `loading.tsx`.
 */
export function PhonePanel({ result }: { result: PhoneNumbersResult }) {
  switch (result.kind) {
    case "ok":
      return result.data.configured ? <PhoneWorkspace initial={result.data} /> : <DemoMode />;
    case "unauthorized":
      return (
        <div className="max-w-3xl">
          <Callout tone="danger" title="Signed out" headingLevel={1}>
            Your session has expired. <Link href={SIGN_IN_PATH} className="underline">Sign in again</Link>.
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
            {phoneMessage("subscription_required")}
          </Callout>
        </div>
      );
    case "not_configured":
      return <DemoMode />;
    case "unavailable":
    case "unreachable":
      return (
        <div className="max-w-3xl">
          <Callout tone="danger" title="Can't load your phone numbers" headingLevel={1}>
            {phoneMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined)} Reload
            the page to try again.
          </Callout>
        </div>
      );
  }
}

function PageHeader() {
  return (
    <div className="flex flex-col gap-2">
      <Eyebrow>Telephony</Eyebrow>
      <h1 className="text-h1 font-bold tracking-tight uppercase">Phone</h1>
      <p className="max-w-[72ch] text-base text-muted-foreground">
        Connect a Twilio number so callers reach your receptionist by phone.
      </p>
    </div>
  );
}

/** Telephony isn't set up on this deployment: calm, not an error. */
function DemoMode() {
  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      <PageHeader />
      <Callout
        tone="success"
        title="Phone calls are in demo mode"
        headingLevel={2}
        action={
          <Link href="/dashboard/agent" className={buttonVariants({ size: "sm" })}>
            Open Test chat
          </Link>
        }
      >
        Telephony isn&apos;t set up for this deployment, so no phone number can be connected here. Your
        receptionist works the same way without one: try it in the Test chat on the agent page.
      </Callout>
    </div>
  );
}

function TrialNotice({ trial }: { trial: boolean | null }) {
  return (
    <Callout tone="warning" title="Twilio trial account" headingLevel={2} compact>
      <div className="flex flex-col gap-2">
        <p>
          {trial === null
            ? "Tether couldn't confirm whether your Twilio account is a trial. If it is, these limits apply."
            : "Your Twilio account is a trial, which limits how a number can be used."}
        </p>
        <ul className="list-disc pl-5">
          <li>Only verified caller IDs can call the number.</li>
          <li>Every call starts with Twilio&apos;s trial message before the agent answers.</li>
          <li>Calls are turn-based: the agent speaks, then listens.</li>
        </ul>
        <p>To test, call the number from a phone you have verified in Twilio.</p>
      </div>
    </Callout>
  );
}

type Notice = { tone: "success" | "warning"; title: string; body: ReactNode };

function PhoneWorkspace({ initial }: { initial: PhoneNumbers }) {
  const [numbers, setNumbers] = useState<PhoneNumber[]>(initial.numbers);
  const [available, setAvailable] = useState<AvailableNumber[]>(initial.available);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [target, setTarget] = useState<PhoneNumber | null>(null);

  function replaceNumber(next: PhoneNumber) {
    setNumbers((all) => all.map((n) => (n.id === next.id ? next : n)));
  }

  function associated(created: PhoneNumber) {
    setNotice(null);
    setNumbers((all) => [...all.filter((n) => n.id !== created.id), created]);
    setAvailable((all) => all.filter((n) => n.provider_number_sid !== created.provider_number_sid));
  }

  function removed(id: string, webhooksCleared: boolean | null) {
    setTarget(null);
    setNumbers((all) => all.filter((n) => n.id !== id));
    if (webhooksCleared === true) {
      setNotice({
        tone: "success",
        title: "Number disconnected",
        body: "Calls to this number no longer reach your receptionist.",
      });
    } else if (webhooksCleared === false) {
      setNotice({
        tone: "warning",
        title: "Disconnected, but the Voice URL is still set on Twilio",
        body: (
          <>
            Tether could not clear this number&apos;s Voice URL on Twilio. You can clear it in the{" "}
            <a
              href={TWILIO_CONSOLE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-link underline"
            >
              Twilio console
            </a>
            .
          </>
        ),
      });
    } else {
      setNotice({
        tone: "warning",
        title: "That number was already gone",
        body: "It is no longer connected to your business. Check the Voice URL in the Twilio console if calls still arrive.",
      });
    }
  }

  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      <PageHeader />

      {initial.trial !== false && <TrialNotice trial={initial.trial} />}

      {notice && (
        <div role="status">
          <Callout tone={notice.tone} title={notice.title} compact headingLevel={2}>
            {notice.body}
          </Callout>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle as="h2">Your numbers</CardTitle>
          <CardDescription>Numbers that currently send calls to your receptionist.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {numbers.length === 0 ? (
            <Callout tone="warning" compact headingLevel={3} title="No number connected">
              Callers can&apos;t reach your receptionist by phone yet. Connect one of the numbers below.
            </Callout>
          ) : (
            <ul className="flex flex-col gap-4">
              {numbers.map((number) => (
                <NumberRow
                  key={number.id}
                  number={number}
                  onUpdated={replaceNumber}
                  onGone={(id) => removed(id, null)}
                  onDisconnect={() => setTarget(number)}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle as="h2">Numbers on your Twilio account</CardTitle>
          <CardDescription>
            Numbers on your Twilio account that no business uses yet. Pick one to connect it.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {initial.available_error === "unavailable" && (
            <Callout tone="danger" compact headingLevel={3} title="Can't load your Twilio numbers">
              {phoneMessage("unavailable")} Reload the page to try again.
            </Callout>
          )}
          {initial.available_error === null && available.length === 0 && (
            <Callout tone="warning" compact headingLevel={3} title="No unassigned numbers">
              Every number on your Twilio account is already connected. Numbers are managed in your Twilio
              account, then show up here.
            </Callout>
          )}
          {available.length > 0 && (
            <ul className="flex flex-col gap-3">
              {available.map((item) => (
                <AvailableRow key={item.provider_number_sid} item={item} onAssociated={associated} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <DisconnectDialog target={target} onClose={() => setTarget(null)} onDone={removed} />
    </div>
  );
}

function NumberRow({
  number,
  onUpdated,
  onGone,
  onDisconnect,
}: {
  number: PhoneNumber;
  onUpdated: (next: PhoneNumber) => void;
  onGone: (id: string) => void;
  onDisconnect: () => void;
}) {
  const { getToken } = useAuth();
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function test() {
    setError(null);
    setTesting(true);
    const token = await getToken();
    const result = token ? await testPhoneNumber(token, number.id) : null;
    setTesting(false);

    if (!result) {
      setError(SESSION_EXPIRED);
    } else if (result.kind === "ok") {
      onUpdated(result.data);
    } else if (result.kind === "not_found") {
      onGone(number.id);
    } else {
      setError(phoneMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined));
    }
  }

  const connected = number.status === "connected";

  return (
    <li className="flex flex-col gap-3 rounded-md border border-border p-4">
      <p className="font-mono text-lg font-bold break-words">{formatPhoneNumber(number.phone_number)}</p>
      {connected ? (
        <p className="flex items-center gap-2 text-base font-bold">
          <CircleCheck aria-hidden className="size-5 shrink-0" />
          {number.verified_at ? "Connected · webhooks verified" : "Connected · not verified yet"}
        </p>
      ) : (
        <p className="flex items-start gap-2 text-base font-bold">
          <TriangleAlert aria-hidden className="mt-0.5 size-5 shrink-0" />
          Needs attention
        </p>
      )}
      {!connected && <FormError>{numberErrorMessage(number.last_error)}</FormError>}
      {connected && number.last_error === "unavailable" && (
        <FormError>{numberErrorMessage("unavailable")}</FormError>
      )}
      {error && <FormError>{error}</FormError>}
      <div className="flex flex-wrap gap-3">
        <Button type="button" variant="outline" size="sm" disabled={testing} onClick={() => void test()}>
          <RefreshCw aria-hidden />
          {testing ? "Testing…" : "Test"}
        </Button>
        <Button type="button" variant="destructive" size="sm" disabled={testing} onClick={onDisconnect}>
          <Unplug aria-hidden />
          Disconnect
        </Button>
      </div>
    </li>
  );
}

function AvailableRow({
  item,
  onAssociated,
}: {
  item: AvailableNumber;
  onAssociated: (created: PhoneNumber) => void;
}) {
  const { getToken } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const display = formatPhoneNumber(item.phone_number);

  async function associate() {
    setError(null);
    setPending(true);
    const token = await getToken();
    const result = token ? await associatePhoneNumber(token, item.provider_number_sid) : null;
    setPending(false);

    if (!result) {
      setError(SESSION_EXPIRED);
    } else if (result.kind === "ok") {
      onAssociated(result.data);
    } else {
      setError(phoneMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined));
    }
  }

  return (
    <li className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border p-4">
      <div className="flex min-w-0 flex-col gap-1">
        <p className="font-mono text-base font-bold break-words">{display}</p>
        {item.friendly_name && item.friendly_name !== item.phone_number && (
          <p className="text-sm text-muted-foreground break-words">{item.friendly_name}</p>
        )}
        {error && <FieldError>{error}</FieldError>}
      </div>
      <Button
        type="button"
        size="sm"
        disabled={pending}
        aria-label={`Connect ${display}`}
        onClick={() => void associate()}
      >
        <PhoneCall aria-hidden />
        {pending ? "Connecting…" : "Connect"}
      </Button>
    </li>
  );
}

function DisconnectDialog({
  target,
  onClose,
  onDone,
}: {
  target: PhoneNumber | null;
  onClose: () => void;
  onDone: (id: string, webhooksCleared: boolean | null) => void;
}) {
  const { getToken } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    setError(null);
    onClose();
  }

  async function confirm() {
    if (!target) return;
    setError(null);
    setBusy(true);
    const token = await getToken();
    const result = token ? await disconnectPhoneNumber(token, target.id) : null;
    setBusy(false);

    if (!result) {
      setError(SESSION_EXPIRED);
    } else if (result.kind === "ok") {
      onDone(target.id, result.webhooksCleared);
    } else if (result.kind === "not_found") {
      onDone(target.id, null);
    } else {
      setError(phoneMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined));
    }
  }

  return (
    <Dialog open={target !== null} onClose={close} title="Disconnect number?">
      <div className="flex flex-col gap-5">
        {error && <FormError>{error}</FormError>}
        <p className="text-base text-muted-foreground">
          Calls to {target ? formatPhoneNumber(target.phone_number) : "this number"} will stop reaching
          your receptionist. The number stays on your Twilio account.
        </p>
        <div className="flex flex-wrap justify-end gap-3">
          <Button type="button" variant="outline" onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button type="button" variant="destructive" disabled={busy} onClick={() => void confirm()}>
            {busy ? "Disconnecting…" : "Disconnect"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
