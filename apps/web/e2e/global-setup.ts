import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { clerkSetup } from "@clerk/testing/playwright";

const REQUIRED = [
  "E2E_CLERK_PUBLISHABLE_KEY",
  "E2E_CLERK_SECRET_KEY",
  "E2E_CLERK_ISSUER",
  "E2E_CLERK_USER_EMAIL",
  "E2E_CLERK_USER_PASSWORD",
] as const;

/** Fetches a Clerk testing token for the run, then empties the E2E user's business so the
 * journey always starts at onboarding. The reset refuses a non-local database. */
export default async function globalSetup() {
  const missing = REQUIRED.filter((name) => !process.env[name]);
  if (missing.length > 0) {
    throw new Error(
      `E2E needs these environment variables (Clerk dev instance): ${missing.join(", ")}. ` +
        "See the 'E2E (slice 17)' section in apps/web/CLAUDE.md.",
    );
  }

  await clerkSetup();

  const apiDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../api");
  const output = execFileSync(
    "uv",
    ["run", "--directory", apiDir, "python", "-m", "seeds.e2e_reset", "--email", process.env.E2E_CLERK_USER_EMAIL!],
    {
      encoding: "utf8",
      env: {
        ...process.env,
        TETHER_ENV_FILE: "",
        ENVIRONMENT: "ci",
        DATABASE_URL: process.env.E2E_DATABASE_URL ?? "postgresql://tether:tether@127.0.0.1:5432/tether",
        CLERK_ISSUER: process.env.E2E_CLERK_ISSUER!,
        STRIPE_SECRET_KEY: "sk_test_e2e_placeholder",
        STRIPE_WEBHOOK_SECRET: "whsec_e2e_placeholder",
        STRIPE_PRICE_ID: "price_e2e_placeholder",
        APP_BASE_URL: "http://localhost:3000",
      },
    },
  );
  console.log(output.trim());
}
