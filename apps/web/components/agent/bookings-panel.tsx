import type { ReactNode } from "react";
import { Ban, CalendarCheck, CircleCheck, Clock, TriangleAlert } from "lucide-react";

import type { BookingRead, BookingStatus } from "@/lib/api/client";
import { formatSlotLabel } from "@/components/settings/availability-messages";
import { Callout } from "@/components/ui/callout";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** What the panel shows; the test chat owns the state and passes it down. */
export type BookingsState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; bookings: BookingRead[] }
  | { kind: "error"; message: string };

const STATUS = {
  confirmed: { label: "Confirmed", Icon: CircleCheck, bar: "border-l-success", note: null },
  pending: { label: "In progress", Icon: Clock, bar: "border-l-border", note: null },
  failed: {
    label: "Not booked",
    Icon: TriangleAlert,
    bar: "border-l-warning",
    note: "This time was not booked.",
  },
  needs_reconciliation: {
    label: "Needs review",
    Icon: TriangleAlert,
    bar: "border-l-warning",
    note: "We could not confirm this one. It may already be on your calendar, so check it there.",
  },
  cancelled: { label: "Cancelled", Icon: Ban, bar: "border-l-border", note: null },
} satisfies Record<
  BookingStatus,
  { label: string; Icon: typeof Ban; bar: string; note: string | null }
>;

export function bookingStatusLabel(status: BookingStatus): string {
  return STATUS[status].label;
}

/**
 * "2026-10-09" -> "Fri, Oct 9". The date is the business-local one, so it is
 * formatted in UTC from its own parts and never shifted to the viewer's zone.
 */
export function formatBookingDate(date: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) return date;
  const value = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(value);
}

export function BookingsPanel({
  state,
  onRetry,
  className,
}: {
  state: BookingsState;
  onRetry: () => void;
  className?: string;
}) {
  let body: ReactNode;
  switch (state.kind) {
    case "idle":
      body = (
        <Quiet>
          No chat yet. Bookings the receptionist makes in this chat will show up here.
        </Quiet>
      );
      break;
    case "loading":
      body = (
        <div role="status" className="flex flex-col gap-3">
          <span className="sr-only">Loading bookings</span>
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      );
      break;
    case "error":
      body = (
        <Callout
          tone="danger"
          title="Bookings did not load"
          headingLevel={3}
          compact
          action={
            <button type="button" onClick={onRetry} className="text-sm font-medium underline">
              Retry
            </button>
          }
        >
          {state.message}
        </Callout>
      );
      break;
    case "ok":
      body =
        state.bookings.length === 0 ? (
          <Quiet>No bookings yet. Ask for a time in the chat and it will appear here.</Quiet>
        ) : (
          <ul className="flex flex-col gap-3">
            {state.bookings.map((booking) => (
              <BookingItem key={booking.id} booking={booking} />
            ))}
          </ul>
        );
      break;
  }

  return (
    <section
      aria-labelledby="chat-bookings-title"
      className={cn(
        "flex min-w-0 flex-col gap-4 rounded-md border border-border bg-card p-6 shadow-card",
        className,
      )}
    >
      <h2
        id="chat-bookings-title"
        className="text-base font-bold tracking-tight uppercase"
      >
        Bookings in this chat
      </h2>
      {body}
    </section>
  );
}

function Quiet({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 text-sm text-muted-foreground">
      <CalendarCheck aria-hidden className="mt-0.5 size-4 shrink-0" />
      <p>{children}</p>
    </div>
  );
}

function BookingItem({ booking }: { booking: BookingRead }) {
  const { label, Icon, bar, note } = STATUS[booking.status];

  return (
    <li className={cn("flex flex-col gap-1 rounded-md border border-l-[6px] border-border p-4", bar)}>
      <p className="flex items-center gap-1.5 text-sm font-bold uppercase">
        <Icon aria-hidden className="size-4 shrink-0" />
        <span>{label}</span>
      </p>
      <p className="text-base font-semibold break-words">{booking.service_name}</p>
      <p className="text-sm text-muted-foreground">
        {formatBookingDate(booking.date)}, {formatSlotLabel(booking.label)}
      </p>
      <p className="text-sm text-muted-foreground break-words">For {booking.customer_name}</p>
      {note && <p className="text-sm text-muted-foreground">{note}</p>}
    </li>
  );
}
