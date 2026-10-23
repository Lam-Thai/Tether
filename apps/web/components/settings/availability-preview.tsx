"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { CalendarSearch, Clock } from "lucide-react";

import {
  getAvailability,
  listServices,
  type Availability,
  type ServiceRead,
} from "@/lib/api/client";
import { availabilityReasonMessage, formatSlotLabel } from "@/components/settings/availability-messages";
import { calendarMessage } from "@/components/settings/calendar-messages";
import { Button, buttonVariants } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormError } from "@/components/ui/form-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

type ServicesState =
  | { kind: "loading" }
  | { kind: "ready"; services: ServiceRead[] }
  | { kind: "error"; message: string };

type PreviewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; data: Availability };

const subscribeNever = () => () => {};

/** The viewer's local calendar day as `YYYY-MM-DD`; `""` on the server, so nothing mismatches on hydration. */
function localToday(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * "Preview availability": asks the API what the receptionist would offer for
 * one service on one day. Every time on screen is a `label` from the response.
 * Only rendered for a connected calendar; the caller shows a note otherwise.
 */
export function AvailabilityPreview() {
  const { getToken } = useAuth();
  const uid = useId();
  const [services, setServices] = useState<ServicesState>({ kind: "loading" });
  const [serviceChoice, setServiceChoice] = useState<string | null>(null);
  const [dateChoice, setDateChoice] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const requestId = useRef(0);

  const today = useSyncExternalStore(subscribeNever, localToday, () => "");

  const latest = useRef({ getToken });
  useEffect(() => {
    latest.current = { getToken };
  });

  async function loadServices() {
    // A rejected getToken (e.g. offline) is `undefined`: show the error with its retry.
    const token = await latest.current.getToken().catch(() => undefined);
    if (token === undefined) {
      setServices({ kind: "error", message: calendarMessage("unknown") });
      return;
    }
    const result = token ? await listServices(token) : ({ kind: "unauthorized" } as const);
    if (result.kind === "ok") {
      setServices({ kind: "ready", services: result.data.services.filter((s) => s.active) });
    } else {
      setServices({
        kind: "error",
        message: calendarMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined),
      });
    }
  }

  useEffect(() => {
    void loadServices();
  }, []);

  function retryServices() {
    setServices({ kind: "loading" });
    void loadServices();
  }

  function invalidate() {
    // A result belongs to the service and date it was fetched for.
    requestId.current += 1;
    setPreview({ kind: "idle" });
  }

  const date = dateChoice ?? today;

  async function check(serviceId: string) {
    const id = ++requestId.current;
    setPreview({ kind: "loading" });
    // Never leave the controls disabled in "loading" when Clerk can't issue a token.
    const token = await getToken().catch(() => undefined);
    if (token === undefined) {
      if (id === requestId.current) setPreview({ kind: "error", message: calendarMessage("unknown") });
      return;
    }
    const result = token ? await getAvailability(token, serviceId, date) : ({ kind: "unauthorized" } as const);
    if (id !== requestId.current) return;

    if (result.kind === "ok") {
      setPreview({ kind: "done", data: result.data });
    } else if (result.kind === "service_not_found") {
      setPreview({ kind: "error", message: "That service no longer exists. Reload the page and pick another." });
    } else if (result.kind === "validation") {
      setPreview({ kind: "error", message: result.message });
    } else {
      setPreview({
        kind: "error",
        message: calendarMessage(result.kind, result.kind === "unreachable" ? result.reason : undefined),
      });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2">Preview availability</CardTitle>
        <CardDescription>
          See the times the receptionist would offer for a service on a given day, using your hours,
          booking rules and calendar.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {services.kind === "loading" && (
          <div role="status" aria-busy="true" className="flex flex-col gap-2">
            <span className="sr-only">Loading your services</span>
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-11 w-full" />
          </div>
        )}

        {services.kind === "error" && (
          <Callout
            tone="danger"
            compact
            headingLevel={3}
            title="Can't load your services"
            action={
              <Button type="button" variant="outline" size="sm" onClick={retryServices}>
                Try again
              </Button>
            }
          >
            {services.message}
          </Callout>
        )}

        {services.kind === "ready" && services.services.length === 0 && (
          <Callout
            tone="warning"
            compact
            headingLevel={3}
            title="No active services"
            action={
              <Link href="/dashboard/agent" className={buttonVariants({ variant: "outline", size: "sm" })}>
                Open Agent Builder
              </Link>
            }
          >
            Availability depends on how long a service takes. Add or turn on a service first.
          </Callout>
        )}

        {services.kind === "ready" && services.services.length > 0 && (
          <PreviewForm
            uid={uid}
            services={services.services}
            serviceId={serviceChoice ?? services.services[0]!.id}
            date={date}
            pending={preview.kind === "loading"}
            onService={(value) => {
              setServiceChoice(value);
              invalidate();
            }}
            onDate={(value) => {
              setDateChoice(value);
              invalidate();
            }}
            onCheck={(serviceId) => void check(serviceId)}
          />
        )}

        <div role="status" aria-live="polite" className="flex flex-col gap-4">
          {preview.kind === "loading" && (
            <div aria-busy="true" className="flex flex-col gap-2">
              <span className="sr-only">Checking availability</span>
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-11 w-full" />
            </div>
          )}
          {preview.kind === "error" && <FormError>{preview.message}</FormError>}
          {preview.kind === "done" && <PreviewResult data={preview.data} />}
        </div>
      </CardContent>
    </Card>
  );
}

function PreviewForm({
  uid,
  services,
  serviceId,
  date,
  pending,
  onService,
  onDate,
  onCheck,
}: {
  uid: string;
  services: ServiceRead[];
  serviceId: string;
  date: string;
  pending: boolean;
  onService: (id: string) => void;
  onDate: (date: string) => void;
  onCheck: (serviceId: string) => void;
}) {
  return (
    <form
      className="grid grid-cols-1 items-end gap-4 sm:grid-cols-[1fr_auto_auto]"
      onSubmit={(event) => {
        event.preventDefault();
        if (!pending && date !== "") onCheck(serviceId);
      }}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${uid}-service`}>Service</Label>
        <Select
          id={`${uid}-service`}
          value={serviceId}
          onChange={(event) => onService(event.target.value)}
          disabled={pending}
        >
          {services.map((service) => (
            <option key={service.id} value={service.id}>
              {service.name} ({service.duration_minutes} min)
            </option>
          ))}
        </Select>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${uid}-date`}>Date</Label>
        <Input
          id={`${uid}-date`}
          type="date"
          value={date}
          onChange={(event) => onDate(event.target.value)}
          disabled={pending}
        />
      </div>
      <Button type="submit" disabled={pending || date === ""}>
        <CalendarSearch aria-hidden />
        {pending ? "Checking…" : "Check availability"}
      </Button>
    </form>
  );
}

function PreviewResult({ data }: { data: Availability }) {
  const where = (
    <>
      Times are in <span className="font-mono text-foreground">{data.timezone}</span>.
    </>
  );

  if (data.status !== "available") {
    const unreachable = data.status === "calendar_unavailable";
    return (
      <Callout
        tone={unreachable ? "danger" : "warning"}
        compact
        headingLevel={3}
        title={unreachable ? "Can't check the calendar" : "Nothing to offer"}
      >
        {availabilityReasonMessage(data.reason)}
      </Callout>
    );
  }

  const heading = data.service ? `${data.service.name} on ${data.date}` : data.date;

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        {heading}. {where}
      </p>

      {data.offered.length > 0 ? (
        <div className="flex flex-col gap-2">
          <h3 className="text-base font-bold tracking-tight uppercase">The receptionist would offer</h3>
          <ul className="flex flex-wrap gap-3">
            {data.offered.map((slot) => (
              <li
                key={slot.start}
                className="flex items-center gap-2 rounded-md border border-border bg-accent px-4 py-2 text-lg font-bold text-accent-foreground"
              >
                <Clock aria-hidden className="size-4" />
                {formatSlotLabel(slot.label)}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">There are no times to offer for that day.</p>
      )}

      {data.slots.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">All free start times ({data.slots.length})</h3>
          <ul className="flex flex-wrap gap-2">
            {data.slots.map((slot) => (
              <li
                key={slot.start}
                className="rounded-sm border border-border px-2 py-1 font-mono text-sm text-muted-foreground"
              >
                {formatSlotLabel(slot.label)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
