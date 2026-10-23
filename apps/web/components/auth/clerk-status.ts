/**
 * Clerk's custom-flow results can be "not done yet" without being errors:
 * a sign-up that still needs a field, a sign-in that needs an emailed code.
 * These helpers turn those states into honest messages, so the forms never
 * blame the owner's code or password for a step the form doesn't collect.
 */

/** Fields Tether's sign-up form collects itself. */
const COLLECTED_FIELDS = new Set(["email_address", "password"]);

const FIELD_LABELS: Record<string, string> = {
  username: "a username",
  first_name: "a first name",
  last_name: "a last name",
  legal_accepted: "accepting the terms",
  phone_number: "a phone number",
};

/** The fields Clerk still requires that this form never asks for. */
export function uncollectedFields(missingFields: readonly string[] | undefined): string[] {
  return (missingFields ?? []).filter((field) => !COLLECTED_FIELDS.has(field));
}

/** "a username", "a username and a first name", "a, b and c". */
export function describeFields(fields: readonly string[]): string {
  const labels = fields.map((field) => FIELD_LABELS[field] ?? field.replaceAll("_", " "));
  if (labels.length <= 1) return labels.join("");
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

export function signUpBlockedMessage(fields: readonly string[], emailVerified: boolean): string {
  const lead = emailVerified
    ? "Your email is verified, but sign-up can't finish yet"
    : "Sign-up can't finish right now";
  return `${lead}: Tether's account settings also require ${describeFields(fields)}, which this form doesn't ask for. This is a problem on our side, not with your details.`;
}

/** The email-code factor Clerk offers for a second step, if any. */
export function emailCodeFactor(
  factors: readonly { strategy: string; emailAddressId?: string; safeIdentifier?: string }[] | null | undefined,
): { emailAddressId: string; safeIdentifier?: string } | null {
  const factor = factors?.find((f) => f.strategy === "email_code" && f.emailAddressId);
  return factor?.emailAddressId
    ? { emailAddressId: factor.emailAddressId, safeIdentifier: factor.safeIdentifier }
    : null;
}
