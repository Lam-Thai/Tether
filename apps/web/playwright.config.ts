import { defineConfig, devices } from "@playwright/test";

/**
 * MVP critical-journey E2E (slice 17). Real Next.js app + real FastAPI app in
 * E2E_FAKE_PROVIDERS=1 mode (Gemini, Stripe and Google Calendar faked in-process) on a
 * local Postgres, with REAL Clerk dev-instance auth through @clerk/testing.
 *
 * Secrets come from the environment only (CI secrets, or exported by the developer):
 *   E2E_CLERK_PUBLISHABLE_KEY, E2E_CLERK_SECRET_KEY, E2E_CLERK_ISSUER,
 *   E2E_CLERK_USER_EMAIL (a `+clerk_test` address), E2E_CLERK_USER_PASSWORD.
 * No .env file is read. The 100% pass requirement means NO retries: a flake is a failure.
 */
const WEB_ORIGIN = "http://localhost:3000";
const API_ORIGIN = "http://127.0.0.1:8000";
const isCI = Boolean(process.env.CI);

const publishableKey = process.env.E2E_CLERK_PUBLISHABLE_KEY ?? "";
const secretKey = process.env.E2E_CLERK_SECRET_KEY ?? "";
const issuer = process.env.E2E_CLERK_ISSUER ?? "";

// @clerk/testing's clerkSetup() reads these names; set them in the config so the
// main process, the workers and global setup all agree.
process.env.CLERK_PUBLISHABLE_KEY = publishableKey;
process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = publishableKey;
process.env.CLERK_SECRET_KEY = secretKey;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "*.spec.ts",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: isCI,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: WEB_ORIGIN,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 20_000,
    navigationTimeout: 60_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Migrates, then serves the API with the fake providers (see apps/api/CLAUDE.md).
      command: "sh ../api/scripts/e2e-server.sh",
      url: `${API_ORIGIN}/health`,
      timeout: 180_000,
      reuseExistingServer: !isCI,
      stdout: "pipe",
      stderr: "pipe",
      env: { E2E_CLERK_ISSUER: issuer, E2E_WEB_ORIGIN: WEB_ORIGIN },
    },
    {
      command: isCI ? "pnpm build && pnpm start" : "pnpm dev",
      url: WEB_ORIGIN,
      timeout: 420_000,
      reuseExistingServer: !isCI,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        NEXT_PUBLIC_API_BASE_URL: API_ORIGIN,
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishableKey,
        CLERK_PUBLISHABLE_KEY: publishableKey,
        CLERK_SECRET_KEY: secretKey,
      },
    },
  ],
});
