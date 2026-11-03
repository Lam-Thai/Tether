// Frontend bundle scan (slice 17): the browser must never receive a server secret.
//
// What it looks for in the files Next serves to browsers:
// - the actual VALUE of any server secret the build environment held (CI builds with a
//   unique canary value for every name in SECRET_ENV_NAMES, so a secret inlined by any
//   route - a `NEXT_PUBLIC_` rename, `env` in next.config, a bad import - shows up here);
// - strings shaped like real secrets (Stripe/Clerk secret keys, webhook secrets, Google
//   API keys, database URLs with a password);
// - a `NEXT_PUBLIC_` name that sounds like a secret (`NEXT_PUBLIC_STRIPE_SECRET_KEY`):
//   the public prefix inlines the value into the bundle, so a secret must never wear it.
// A bare server-only NAME is not a finding: third-party client code reads
// `process.env.CLERK_SECRET_KEY`, which is `undefined` in a browser because Next only
// inlines `NEXT_PUBLIC_` values.

/** Server-only env names whose values must never reach a browser. */
export const SECRET_ENV_NAMES: readonly string[] = [
  "CLERK_SECRET_KEY",
  "CLERK_WEBHOOK_SECRET",
  "STRIPE_SECRET_KEY",
  "STRIPE_WEBHOOK_SECRET",
  "GEMINI_API_KEY",
  "TWILIO_AUTH_TOKEN",
  "TWILIO_TEST_AUTH_TOKEN",
  "DATABASE_URL",
  "GOOGLE_OAUTH_CLIENT_SECRET",
  "CALENDAR_TOKEN_KEYS",
  "OAUTH_STATE_SECRET",
  "E2E_CLERK_USER_PASSWORD",
];

// Shapes of real secret values. Publishable keys (`pk_`) are public by design.
const SECRET_VALUE_RULES: ReadonlyArray<{ rule: string; pattern: RegExp }> = [
  { rule: "stripe-secret-key", pattern: /\b[sr]k_(?:test|live)_[A-Za-z0-9]{8,}/ },
  { rule: "stripe-webhook-secret", pattern: /\bwhsec_[A-Za-z0-9+/=]{8,}/ },
  { rule: "clerk-secret-key", pattern: /\bsk_(?:test|live)_[A-Za-z0-9+/=]{20,}/ },
  { rule: "google-api-key", pattern: /\bAIza[0-9A-Za-z_-]{30,}/ },
  { rule: "database-url", pattern: /\bpostgres(?:ql)?(?:\+\w+)?:\/\/[^\s:/@"'`]+:[^\s@"'`]+@/ },
];

const PUBLIC_SECRET_NAME =
  /\bNEXT_PUBLIC_[A-Z0-9_]*(?:SECRET|PASSWORD|PRIVATE|TOKEN|API_KEY|DATABASE_URL)[A-Z0-9_]*\b/g;

// A secret value shorter than this is too generic to search for without false positives.
const MIN_VALUE_LENGTH = 12;

export interface BundleFinding {
  path: string;
  rule: string;
  /** What matched, with the secret's value itself never repeated in full. */
  detail: string;
}

function hint(value: string): string {
  return value.length <= 8 ? "***" : `${value.slice(0, 4)}***`;
}

/**
 * Every place a client file carries a server secret: a value the build environment held
 * (`secretValues`), a value shaped like a secret, or a secret-sounding public env name.
 */
export function findSecretsInBundle(
  files: ReadonlyArray<{ path: string; content: string }>,
  secretValues: Readonly<Record<string, string | undefined>> = {},
): BundleFinding[] {
  const findings: BundleFinding[] = [];
  const values = Object.entries(secretValues).filter(
    (entry): entry is [string, string] =>
      typeof entry[1] === "string" && entry[1].length >= MIN_VALUE_LENGTH,
  );
  for (const { path, content } of files) {
    for (const [name, value] of values) {
      if (content.includes(value)) {
        findings.push({ path, rule: "secret-env-value", detail: `${name}=${hint(value)}` });
      }
    }
    for (const { rule, pattern } of SECRET_VALUE_RULES) {
      const match = pattern.exec(content);
      if (match) findings.push({ path, rule, detail: hint(match[0]) });
    }
    for (const name of new Set(content.match(PUBLIC_SECRET_NAME) ?? [])) {
      findings.push({ path, rule: "public-secret-name", detail: name });
    }
  }
  return findings;
}
