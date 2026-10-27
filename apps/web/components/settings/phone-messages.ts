import type { PhoneNumberError } from "@/lib/api/client";

export const SESSION_EXPIRED = "Your session has expired. Please sign in again.";

export const TWILIO_CONSOLE_URL = "https://console.twilio.com/";

const NOT_FOUND = "That number is no longer on your Twilio account. Reload the page to see what is available.";
const IN_USE = "That number is already connected to another business.";
const NOT_CONFIGURED = "Phone calls aren't set up for this deployment yet, so no number can be connected.";
const UNAVAILABLE = "Twilio didn't respond. Try again in a minute.";

const MESSAGES: Record<string, string> = {
  not_found: NOT_FOUND,
  in_use: IN_USE,
  not_configured: NOT_CONFIGURED,
  unavailable: UNAVAILABLE,
  subscription_required: "Your plan is not active. Choose a plan to use phone numbers.",
  unauthorized: SESSION_EXPIRED,
  // Raw API error codes, for callers that pass the code itself.
  phone_number_not_found: NOT_FOUND,
  phone_number_in_use: IN_USE,
  telephony_not_configured: NOT_CONFIGURED,
  telephony_unavailable: UNAVAILABLE,
};

/** Plain-language message for any phone result kind or API error code; `reason` is used for `unreachable`. */
export function phoneMessage(kind: string, reason?: string): string {
  if (kind === "unreachable" && reason) return reason;
  return MESSAGES[kind] ?? "Something went wrong. Please try again.";
}

const NUMBER_ERRORS: Record<PhoneNumberError, string> = {
  voice_url_mismatch:
    "This number's Voice URL on Twilio no longer points at Tether, so calls won't reach the agent. Test to check again, or connect the number again to fix it.",
  status_callback_mismatch:
    "This number's status callback on Twilio no longer points at Tether. Test to check again, or connect the number again to fix it.",
  number_missing: "This number is no longer on your Twilio account. Disconnect it here and pick another.",
  unavailable: "Tether couldn't reach Twilio at the last check. Test again in a minute.",
};

export function numberErrorMessage(error: PhoneNumberError | null): string {
  return error ? NUMBER_ERRORS[error] : "Tether could not confirm this number's setup. Test to check again.";
}

/** `+15551234567` becomes `+1 555 123 4567`; other numbers are shown as stored. */
export function formatPhoneNumber(e164: string): string {
  const match = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164);
  return match ? `+1 ${match[1]} ${match[2]} ${match[3]}` : e164;
}
