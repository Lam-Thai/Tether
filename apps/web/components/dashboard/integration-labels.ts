import type { IntegrationError } from "@/lib/api/client";

/** "reconnect_needed" -> "Reconnect needed"; an empty string gets the fallback. */
export function deriveLabel(value: string, fallback: string): string {
  const words = value.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : fallback;
}

export type StatusTone = "neutral" | "success" | "warning";

export interface StatusView {
  tone: StatusTone;
  label: string;
}

/** Calendar connection state. Unknown values from a newer backend get a derived, neutral label. */
export function calendarStatusView(status: string, configured: boolean): StatusView {
  if (!configured && status === "not_connected") return { tone: "neutral", label: "Not available" };
  switch (status) {
    case "connected":
      return { tone: "success", label: "Connected" };
    case "reconnect_needed":
      return { tone: "warning", label: "Reconnect needed" };
    case "needs_selection":
      return { tone: "warning", label: "Choose a calendar" };
    case "not_connected":
      return { tone: "neutral", label: "Not connected" };
    default:
      return { tone: "neutral", label: deriveLabel(status, "Unknown") };
  }
}

/** Phone number state. Unknown values get a derived, neutral label. */
export function phoneStatusView(status: string, configured: boolean): StatusView {
  if (!configured && status === "not_connected") return { tone: "neutral", label: "Not available" };
  switch (status) {
    case "connected":
      return { tone: "success", label: "Connected" };
    case "misconfigured":
      return { tone: "warning", label: "Needs attention" };
    case "not_connected":
      return { tone: "neutral", label: "Not connected" };
    default:
      return { tone: "neutral", label: deriveLabel(status, "Unknown") };
  }
}

const SOURCE_LABELS: Record<string, string> = {
  calendar: "Google Calendar",
  phone: "Phone",
  sms: "Text alerts",
};

export function errorSourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? deriveLabel(source, "Integration");
}

const CALENDAR_ERRORS: Record<string, string> = {
  auth_revoked: "Google stopped accepting Tether's access. Reconnect your calendar.",
  reconnect_needed: "Reconnect needed: Tether can't check or book your calendar.",
  unavailable: "Tether couldn't reach Google Calendar at the last check.",
  calendar_missing: "The selected calendar is no longer available. Choose another one.",
};

const PHONE_ERRORS: Record<string, string> = {
  voice_url_mismatch: "The number's Voice URL no longer points at Tether, so calls won't reach your receptionist.",
  status_callback_mismatch: "The number's status callback no longer points at Tether.",
  number_missing: "The number is no longer on your Twilio account.",
  unavailable: "Tether couldn't reach Twilio at the last check.",
};

/** Where an error is fixed. Unknown sources link to the integrations page. */
const FIX_LINKS: Record<string, string> = {
  calendar: "/dashboard/settings/calendar",
  phone: "/dashboard/phone",
  sms: "/dashboard/agent#escalation",
};

export interface ErrorView {
  source: string;
  message: string;
  /** The raw code, shown in mono as a reference for support. */
  code: string;
  fixUrl: string;
}

/**
 * Plain copy for a fixed `(source, code)` pair. The API only sends codes,
 * never provider text, so anything unknown is described from the code itself.
 */
export function describeIntegrationError(error: Pick<IntegrationError, "source" | "code">): ErrorView {
  const { source, code } = error;
  let message: string | undefined;
  if (source === "calendar") message = CALENDAR_ERRORS[code];
  if (source === "phone") message = PHONE_ERRORS[code];
  if (source === "sms") {
    message =
      code === "sms_failed"
        ? "A text alert could not be sent."
        : "A text alert was not delivered.";
  }
  return {
    source: errorSourceLabel(source),
    message: message ?? `${deriveLabel(code, "Unknown problem")}.`,
    code,
    fixUrl: FIX_LINKS[source] ?? "/dashboard/settings/integrations",
  };
}

const READINESS_LABELS: Record<string, string> = {
  persona: "Name and greeting",
  services: "Services",
  hours: "Opening hours",
  calendar: "Google Calendar",
  escalation_contact: "Escalation contact",
  phone: "Phone number",
  test_chat: "Test conversation",
};

export function readinessLabel(key: string): string {
  return READINESS_LABELS[key] ?? deriveLabel(key, "Setup step");
}

/** `2026-09-29` -> `Sep 29`, read as a plain calendar date so no timezone can shift it. */
export function formatRangeDate(date: string, withYear = false): string {
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return date;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    ...(withYear ? { year: "numeric" } : {}),
    timeZone: "UTC",
  }).format(parsed);
}

/** "Last 30 days" for the default window, else the span in days. */
export function rangeSummary(from: string, to: string): { title: string; dates: string } {
  const start = new Date(`${from}T00:00:00Z`).getTime();
  const end = new Date(`${to}T00:00:00Z`).getTime();
  const days = Number.isNaN(start) || Number.isNaN(end) ? null : Math.round((end - start) / 86_400_000) + 1;
  return {
    title: days === null ? "Selected range" : days === 30 ? "Last 30 days" : days === 1 ? "1 day" : `${days} days`,
    dates: `${formatRangeDate(from)} to ${formatRangeDate(to, true)}`,
  };
}
