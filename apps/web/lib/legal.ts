/**
 * The one place for the legal facts the Privacy Policy and Terms of Service
 * state. The owner fills these in; never invent a company name, address,
 * email or jurisdiction in the copy. A `null` renders as a visible
 * "to be confirmed" mark (`components/legal/legal-fact.tsx`) until it is set.
 */

/** The legal name of the business that operates Tether. */
export const LEGAL_NAME: string | null = null;

/** Where users send privacy questions. (Account deletion is self-serve, not by email.) */
export const CONTACT_EMAIL: string | null = null;

/** The governing law for the Terms, e.g. a state and country. */
export const JURISDICTION: string | null = null;

/** When the current text took effect (YYYY-MM-DD). Change it with every edit to either page. */
export const LAST_UPDATED = "2026-10-29";

export const PRIVACY_PATH = "/privacy";
export const TERMS_PATH = "/terms";

/** A YYYY-MM-DD date as "October 29, 2026", read as a calendar date so no timezone moves it. */
export function formatLegalDate(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}
