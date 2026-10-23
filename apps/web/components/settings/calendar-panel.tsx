"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { CalendarDays, CircleCheck, Unplug } from "lucide-react";

import {
  disconnectCalendar,
  getCalendarConnectUrl,
  listCalendars,
  selectCalendar,
  type CalendarOption,
  type CalendarStatus,
  type CalendarStatusResult,
} from "@/lib/api/client";
import { Dialog } from "@/components/agent/faq-dialog";
import { AvailabilityPreview } from "@/components/settings/availability-preview";
import { calendarMessage, GOOGLE_PERMISSIONS_URL, SESSION_EXPIRED } from "@/components/settings/calendar-messages";
import { Button, buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Eyebrow } from "@/components/ui/eyebrow";
import { FieldError, FormError } from "@/components/ui/form-error";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const SIGN_IN_PATH = "/sign-in";

/**
 * Presentational view of `/dashboard/settings/calendar`: switches exhaustively
 * on the `GET /calendar/status` result. Loading lives in the route's `loading.tsx`.
 */
export function CalendarPanel({ status }: { status: CalendarStatusResult }) {
  switch (status.kind) {
    case "ok":
      return <CalendarWorkspace initial={status.data} />;
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
            Choose a plan to connect a calendar.
          </Callout>
        </div>
      );
    case "not_configured":
    case "unavailable":
    case "unreachable":
      return (
        <div className="max-w-3xl">
          <Callout tone="danger" title="Can't load your calendar" headingLevel={1}>
            {calendarMessage(status.kind, status.kind === "unreachable" ? status.reason : undefined)} Reload
            the page to try again.
          </Callout>
        </div>
      );
  }
}

type Notice = { tone: "success" | "warning"; title: string; body: ReactNode };

function CalendarWorkspace({ initial }: { initial: CalendarStatus }) {
  const [current, setCurrent] = useState<CalendarStatus>(initial);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [changing, setChanging] = useState(false);
  const [confirming, setConfirming] = useState(false);

  function reconnectNeeded() {
    setChanging(false);
    setCurrent({ status: "reconnect_needed", calendar_id: null, calendar_name: null, last_error: "auth_revoked", last_synced_at: null });
  }

  function disconnected(revoked: boolean) {
    setConfirming(false);
    setChanging(false);
    setCurrent({ status: "not_connected", calendar_id: null, calendar_name: null, last_error: null, last_synced_at: null });
    setNotice(
      revoked
        ? {
            tone: "success",
            title: "Calendar disconnected",
            body: "Tether no longer has access to your Google Calendar.",
          }
        : {
            tone: "warning",
            title: "Disconnected, but Google didn't confirm",
            body: (
              <>
                Tether deleted its copy of the connection, but Google didn&apos;t confirm that access was
                revoked. To be sure, remove Tether on the{" "}
                <a
                  href={GOOGLE_PERMISSIONS_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-link underline"
                >
                  Google account permissions page
                </a>
                .
              </>
            ),
          },
    );
  }

  return (
    <div className="flex w-full max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Eyebrow>Google Calendar</Eyebrow>
        <h1 className="text-h1 font-bold tracking-tight uppercase">Calendar</h1>
        <p className="max-w-[72ch] text-base text-muted-foreground">
          Connect the calendar where Tether checks availability and books appointments.
        </p>
      </div>

      {notice && (
        <div role="status">
          <Callout tone={notice.tone} title={notice.title} compact headingLevel={2}>
            {notice.body}
          </Callout>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle as="h2">Connection</CardTitle>
          <CardDescription>
            Tether asks Google only to list your calendars, read busy times and create events on
            calendars you own.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {current.status === "not_connected" && (
            <Callout
              tone="warning"
              compact
              headingLevel={3}
              title="No calendar connected"
              action={<ConnectButton label="Connect Google Calendar" onStart={() => setNotice(null)} />}
            >
              Without a calendar the receptionist can&apos;t see when you&apos;re free, so it can&apos;t
              book appointments.
            </Callout>
          )}

          {current.status === "needs_selection" && (
            <div className="flex flex-col gap-4">
              <p className="flex items-center gap-2 text-base font-medium">
                <CalendarDays aria-hidden className="size-5 shrink-0" />
                Google account connected. Choose a calendar.
              </p>
              {current.last_error === "calendar_missing" && (
                <FormError>The calendar you chose is no longer in your account. Pick another one.</FormError>
              )}
              <CalendarPicker
                currentId={null}
                onSaved={(next) => {
                  setNotice(null);
                  setCurrent(next);
                }}
                onReconnectNeeded={reconnectNeeded}
              />
              <div>
                <Button type="button" variant="destructive" size="sm" onClick={() => setConfirming(true)}>
                  <Unplug aria-hidden />
                  Disconnect
                </Button>
              </div>
            </div>
          )}

          {current.status === "connected" && (
            <div className="flex flex-col gap-4">
              <p className="flex items-center gap-2 text-base font-bold break-words">
                <CircleCheck aria-hidden className="size-5 shrink-0" />
                {current.calendar_name ? `Connected · "${current.calendar_name}"` : "Connected"}
              </p>
              {current.last_error === "unavailable" && (
                <FormError>Tether couldn&apos;t reach Google Calendar at its last check.</FormError>
              )}
              {changing ? (
                <CalendarPicker
                  currentId={current.calendar_id}
                  onSaved={(next) => {
                    setNotice(null);
                    setChanging(false);
                    setCurrent(next);
                  }}
                  onReconnectNeeded={reconnectNeeded}
                  onCancel={() => setChanging(false)}
                />
              ) : (
                <div className="flex flex-wrap gap-3">
                  <Button type="button" variant="outline" size="sm" onClick={() => setChanging(true)}>
                    Change calendar
                  </Button>
                  <Button type="button" variant="destructive" size="sm" onClick={() => setConfirming(true)}>
                    <Unplug aria-hidden />
                    Disconnect
                  </Button>
                </div>
              )}
            </div>
          )}

          {current.status === "reconnect_needed" && (
            <Callout
              tone="warning"
              compact
              headingLevel={3}
              title="Reconnect needed"
              action={
                <div className="flex flex-wrap gap-3">
                  <ConnectButton label="Reconnect" onStart={() => setNotice(null)} />
                  <Button type="button" variant="destructive" size="sm" onClick={() => setConfirming(true)}>
                    <Unplug aria-hidden />
                    Disconnect
                  </Button>
                </div>
              }
            >
              Google stopped accepting Tether&apos;s access, so it can&apos;t check or book your calendar.
              Reconnect to pick up where you left off.
            </Callout>
          )}
        </CardContent>
      </Card>

      {current.status === "connected" ? (
        <AvailabilityPreview />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle as="h2">Preview availability</CardTitle>
            <CardDescription>
              Connect a calendar and choose which one to use first. Then you can preview the times the
              receptionist would offer.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <DisconnectDialog open={confirming} onClose={() => setConfirming(false)} onDone={disconnected} />
    </div>
  );
}

/** Starts Google's consent flow: the API makes the URL, this only follows it (checked in the client). */
function ConnectButton({ label, onStart }: { label: string; onStart: () => void }) {
  const { getToken } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const reset = (event: PageTransitionEvent) => {
      if (event.persisted) setPending(false);
    };
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);

  async function start() {
    setError(null);
    onStart();
    setPending(true);
    let result;
    try {
      const token = await getToken();
      result = token ? await getCalendarConnectUrl(token) : ({ kind: "unauthorized" } as const);
    } catch {
      setPending(false);
      setError(calendarMessage("unknown"));
      return;
    }

    if (result.kind === "ok") {
      window.location.assign(result.url);
      return;
    }
    setPending(false);
    setError(calendarMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined));
  }

  return (
    <div className="flex flex-col gap-2">
      <Button type="button" size="sm" disabled={pending} onClick={() => void start()}>
        <CalendarDays aria-hidden />
        {pending ? "Opening Google…" : label}
      </Button>
      {error && <FieldError>{error}</FieldError>}
    </div>
  );
}

type PickerState =
  | { kind: "loading" }
  | { kind: "ready"; calendars: CalendarOption[] }
  | { kind: "error"; message: string };

function CalendarPicker({
  currentId,
  onSaved,
  onReconnectNeeded,
  onCancel,
}: {
  currentId: string | null;
  onSaved: (status: CalendarStatus) => void;
  onReconnectNeeded: () => void;
  onCancel?: () => void;
}) {
  const { getToken } = useAuth();
  const uid = useId();
  const [state, setState] = useState<PickerState>({ kind: "loading" });
  const [choice, setChoice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const latest = useRef({ getToken, onReconnectNeeded });
  useEffect(() => {
    latest.current = { getToken, onReconnectNeeded };
  });

  async function load() {
    const token = await latest.current.getToken();
    const result = token ? await listCalendars(token) : ({ kind: "unauthorized" } as const);
    if (result.kind === "ok") {
      setState({ kind: "ready", calendars: result.calendars });
    } else if (result.kind === "reconnect_needed") {
      latest.current.onReconnectNeeded();
    } else {
      setState({
        kind: "error",
        message: calendarMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined),
      });
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function retry() {
    setState({ kind: "loading" });
    void load();
  }

  async function save(calendarId: string) {
    setSaveError(null);
    setSaving(true);
    const token = await getToken();
    const result = token ? await selectCalendar(token, calendarId) : ({ kind: "unauthorized" } as const);
    setSaving(false);

    if (result.kind === "ok") {
      onSaved(result.data);
    } else if (result.kind === "reconnect_needed") {
      onReconnectNeeded();
    } else {
      setSaveError(calendarMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined));
    }
  }

  if (state.kind === "loading") {
    return (
      <div role="status" aria-busy="true" className="flex flex-col gap-2">
        <span className="sr-only">Loading your calendars</span>
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-11 w-full" />
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <Callout
        tone="danger"
        compact
        headingLevel={3}
        title="Can't load your calendars"
        action={
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            Try again
          </Button>
        }
      >
        {state.message}
      </Callout>
    );
  }

  if (state.calendars.length === 0) {
    return (
      <Callout
        tone="warning"
        compact
        headingLevel={3}
        title="No calendars you own"
        action={
          <Button type="button" variant="outline" size="sm" onClick={retry}>
            Check again
          </Button>
        }
      >
        This Google account has no calendar that you own. Create one in Google Calendar, then check
        again.
      </Callout>
    );
  }

  const selected = choice ?? state.calendars.find((c) => c.id === currentId)?.id ?? "";
  const unchanged = selected === "" || selected === currentId;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!unchanged && !saving) void save(selected);
      }}
    >
      {saveError && <FormError>{saveError}</FormError>}
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${uid}-calendar`}>Calendar for bookings</Label>
        <Select
          id={`${uid}-calendar`}
          value={selected}
          onChange={(event) => setChoice(event.target.value)}
          disabled={saving}
        >
          <option value="" disabled>
            Choose a calendar
          </option>
          {state.calendars.map((calendar) => (
            <option key={calendar.id} value={calendar.id}>
              {calendar.primary ? `${calendar.name} (primary)` : calendar.name}
            </option>
          ))}
        </Select>
        <p className="text-sm text-muted-foreground">Only calendars you own are listed.</p>
      </div>
      <div className="flex flex-wrap gap-3">
        <Button type="submit" size="sm" disabled={unchanged || saving}>
          {saving ? "Saving…" : "Save calendar"}
        </Button>
        {onCancel && (
          <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}

function DisconnectDialog({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: (revoked: boolean) => void;
}) {
  const { getToken } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    setError(null);
    onClose();
  }

  async function confirm() {
    setError(null);
    setBusy(true);
    const token = await getToken();
    const result = token ? await disconnectCalendar(token) : null;
    setBusy(false);

    if (!result) {
      setError(SESSION_EXPIRED);
    } else if (result.kind === "ok") {
      onDone(result.revoked);
    } else if (result.kind === "not_connected") {
      // Already gone on the server: the screen should match, but Google's side is unknown.
      onDone(false);
    } else {
      setError(calendarMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined));
    }
  }

  return (
    <Dialog open={open} onClose={close} title="Disconnect calendar?">
      <div className="flex flex-col gap-5">
        {error && <FormError>{error}</FormError>}
        <p className="text-base text-muted-foreground">
          Tether will stop checking this calendar and can&apos;t book appointments until you connect one
          again. Existing events stay in Google Calendar.
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
