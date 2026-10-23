import type { AvailabilityReason } from "@/lib/api/client";

const REASON_MESSAGES: Record<AvailabilityReason, string> = {
  closed: "Closed that day.",
  hours_not_set: "Business hours aren't set yet, so there are no times to offer. Set them in the Agent Builder.",
  fully_booked: "Fully booked: nothing is free that day for this service.",
  no_time_left: "No time left that day: everything still open is too soon or already past.",
  past_date: "That date has already passed.",
  beyond_horizon: "That date is further ahead than your booking rules allow.",
  service_too_long: "This service is longer than the opening hours that day.",
  too_soon: "That is sooner than your minimum notice allows.",
  not_connected: "No calendar is connected, so availability can't be checked.",
  reconnect_needed: "Tether's access to your Google Calendar needs to be reconnected before availability can be checked.",
  unavailable: "Google Calendar couldn't be reached. Try again shortly.",
};

const FALLBACK = "No times can be offered for that day.";

/** Plain message for the `reason` of a response that has nothing to offer. A null or unknown reason never invents a cause. */
export function availabilityReasonMessage(reason: string | null): string {
  if (reason !== null && reason in REASON_MESSAGES) return REASON_MESSAGES[reason as AvailabilityReason];
  return FALLBACK;
}

/**
 * "14:30" -> "2:30 PM". A label that isn't a 24-hour HH:MM is returned
 * unchanged: the UI never makes up a time the API didn't send.
 */
export function formatSlotLabel(label: string): string {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(label);
  if (!match) return label;
  const hour = Number(match[1]);
  const suffix = hour >= 12 ? "PM" : "AM";
  const twelve = hour % 12 === 0 ? 12 : hour % 12;
  return `${twelve}:${match[2]} ${suffix}`;
}
