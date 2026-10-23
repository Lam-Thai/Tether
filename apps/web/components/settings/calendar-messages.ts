export const GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/permissions";

export const SESSION_EXPIRED = "Your session has expired. Please sign in again.";

const MESSAGES: Record<string, string> = {
  invalid_state:
    "This connection attempt expired or doesn't match your account. Start the connection again.",
  scopes_missing:
    "Google Calendar access was only partly granted, and Tether needs every permission it asks for. Start again and leave every box ticked.",
  grant_incomplete:
    "Google didn't give Tether lasting access. Start again and approve the request.",
  not_configured: "Google Calendar isn't set up on this server yet, so nothing can be connected.",
  unavailable: "Google Calendar can't be reached right now. Try again in a minute.",
  reconnect_needed: "Tether's access to your Google Calendar was removed or has expired. Reconnect to continue.",
  not_connected: "No calendar is connected. Connect one to continue.",
  calendar_not_found: "That calendar is no longer available. Pick another one.",
  invalid_url: "The connection link from the server wasn't a Google link, so it was not opened.",
  unauthorized: SESSION_EXPIRED,
  subscription_required: "Your plan is not active. Choose a plan to use Google Calendar.",
};

/** Plain-language message for any calendar result kind; `reason` is used for `unreachable`. */
export function calendarMessage(kind: string, reason?: string): string {
  if (kind === "unreachable" && reason) return reason;
  return MESSAGES[kind] ?? "Something went wrong. Please try again.";
}

/** Why a Google redirect came back with `error=...`; the raw value is never echoed. */
export function googleErrorMessage(error: string): string {
  if (error === "access_denied") {
    return "You didn't allow access in Google, so nothing was connected.";
  }
  return "Google reported a problem, so nothing was connected.";
}
