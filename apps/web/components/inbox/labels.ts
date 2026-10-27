import type { InteractionOutcome, InteractionRow, InteractionStatus } from "@/lib/api/client";

const INTENT_LABELS: Record<string, string> = {
  BOOK_APPOINTMENT: "Booking",
  RESCHEDULE: "Reschedule",
  CANCEL: "Cancellation",
  URGENT: "Urgent",
  COMPLAINT: "Complaint",
  HUMAN_REQUEST: "Asked for a person",
  PRICE_INQUIRY: "Price question",
  BUSINESS_HOURS: "Business hours",
  LOCATION: "Location",
  FAQ: "FAQ",
  UNKNOWN: "Unclear",
};

/** Known intents get a plain label; others are derived from the string; null is "not set yet". */
export function intentLabel(intent: string | null): string {
  if (intent === null) return "No intent yet";
  const known = INTENT_LABELS[intent];
  if (known) return known;
  const words = intent.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "No intent yet";
}

const OUTCOME_LABELS: Record<InteractionOutcome, string> = {
  booked: "Booked",
  answered: "Answered",
  escalated: "Escalated",
  failed: "Failed",
  abandoned: "Abandoned",
};

export function outcomeLabel(outcome: InteractionOutcome): string {
  return OUTCOME_LABELS[outcome];
}

const STATUS_LABELS: Record<InteractionStatus, string> = {
  in_progress: "In progress",
  completed: "Completed",
  failed: "Failed",
  abandoned: "Abandoned",
};

export function statusLabel(status: InteractionStatus): string {
  return STATUS_LABELS[status];
}

const REASON_LABELS: Record<string, string> = {
  urgent: "Urgent",
  human_requested: "Asked for a person",
  complaint: "Complaint",
  change_request: "Appointment change",
  booking_unresolved: "Booking failed",
  integration_failing: "System problem",
  clarification_exhausted: "Couldn't answer",
};

/** Known reasons get a plain label; an unknown one is derived from the string. */
export function escalationReasonLabel(reason: string): string {
  const known = REASON_LABELS[reason];
  if (known) return known;
  const words = reason.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Needs a person";
}

const SMS_STATUS_LABELS: Record<string, string> = {
  pending: "Sending…",
  disabled: "SMS off",
  logged: "Logged (demo mode)",
  skipped: "Already alerted",
  accepted: "Sending…",
  queued: "Sending…",
  sending: "Sending…",
  sent: "Sent",
  delivered: "Delivered",
  undelivered: "Not delivered",
  failed: "Failed",
};

/** Known SMS statuses get a plain label; an unknown one reads as "Unknown". */
export function smsStatusLabel(status: string): string {
  return SMS_STATUS_LABELS[status] ?? "Unknown";
}

/** "Test chat" for an owner's test conversation, the caller otherwise, else "Unknown caller". */
export function callerLabel(row: Pick<InteractionRow, "channel" | "caller">): string {
  if (row.caller) return row.caller;
  return row.channel === "test_chat" ? "Test chat" : "Unknown caller";
}

/** Newer ICU puts a narrow no-break space before AM/PM; use a plain space so text compares and wraps normally. */
const plain = (text: string) => text.replace(/\u202f/g, " ");

/**
 * Formats an instant in the business's timezone with a fixed en-US locale; never the viewer's.
 * An unknown zone falls back to UTC and an unparsable instant is shown as received.
 */
export function formatBusinessTime(iso: string, timeZone: string, opts: { long?: boolean } = {}): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const options: Intl.DateTimeFormatOptions = {
    month: "short",
    day: "numeric",
    ...(opts.long ? { year: "numeric", timeZoneName: "short" } : {}),
    hour: "numeric",
    minute: "2-digit",
  };
  try {
    return plain(new Intl.DateTimeFormat("en-US", { ...options, timeZone }).format(date));
  } catch {
    return plain(new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" }).format(date));
  }
}
