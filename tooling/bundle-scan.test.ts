import { describe, expect, it } from "vitest";

import { findSecretsInBundle, SECRET_ENV_NAMES } from "./bundle-scan.ts";

const file = (content: string, path = "static/chunks/app.js") => ({ path, content });
// Fixtures shaped like real keys are assembled at runtime, so this file never contains a
// key-shaped literal (GitHub push protection and gitleaks would rightly flag one).
const fake = (...parts: string[]) => parts.join("");

describe("findSecretsInBundle", () => {
  it("passes a client bundle with only public values", () => {
    const bundle = file(
      'const api="https://api.example.com";const pk="pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk";' +
        'fetch(process.env.NEXT_PUBLIC_API_BASE_URL)',
    );
    expect(findSecretsInBundle([bundle])).toEqual([]);
  });

  it("does not flag a bare server-only name: third-party code reads it and gets undefined", () => {
    const clerk = file("d.env.CLERK_SECRET_KEY,d.env.CLERK_API_URL,d.env.NEXT_PUBLIC_CLERK_JS_URL");
    expect(findSecretsInBundle([clerk])).toEqual([]);
  });

  it.each([
    "NEXT_PUBLIC_STRIPE_SECRET_KEY",
    "NEXT_PUBLIC_GEMINI_API_KEY",
    "NEXT_PUBLIC_DATABASE_URL",
    "NEXT_PUBLIC_TWILIO_AUTH_TOKEN",
    "NEXT_PUBLIC_PRIVATE_KEY",
  ])("flags the secret-sounding public name %s", (name) => {
    expect(findSecretsInBundle([file(`const k=process.env.${name};`)])).toEqual([
      { path: "static/chunks/app.js", rule: "public-secret-name", detail: name },
    ]);
  });

  it("does not flag Clerk's public settings", () => {
    const names = ["NEXT_PUBLIC_CLERK_JS_URL", "NEXT_PUBLIC_CLERK_SIGN_IN_URL", "NEXT_PUBLIC_API_BASE_URL"];
    expect(findSecretsInBundle([file(names.join(","))])).toEqual([]);
  });

  it("knows every server secret the web app or the API reads", () => {
    for (const name of ["CLERK_SECRET_KEY", "STRIPE_SECRET_KEY", "GEMINI_API_KEY", "DATABASE_URL"]) {
      expect(SECRET_ENV_NAMES).toContain(name);
    }
  });

  it.each([
    ["stripe-secret-key", fake("sk_", "test_", "51Habcdefghijklmnop")],
    ["stripe-secret-key", fake("rk_", "live_", "abcdefghijklmnop")],
    ["stripe-webhook-secret", fake("whsec_", "abcdefghijklmnop")],
    ["google-api-key", fake("AIza", "SyA1234567890abcdefghijklmnopqrstuv")],
    ["database-url", "postgresql://tether:hunter2@db.example.com/tether"],
  ])("flags a %s-shaped value without repeating it", (rule, value) => {
    const findings = findSecretsInBundle([file(`var x="${value}";`)]);
    expect(findings.map((f) => f.rule)).toContain(rule);
    for (const finding of findings) expect(finding.detail).not.toContain(value);
  });

  it("flags the actual value of a secret the build environment held", () => {
    const secret = fake("sk_", "live_", "ZXhhbXBsZS1zZWNyZXQta2V5LWZvci1jaS1vbmx5");
    const findings = findSecretsInBundle([file(`a="${secret}"`)], { CLERK_SECRET_KEY: secret });
    expect(findings).toContainEqual({
      path: "static/chunks/app.js",
      rule: "secret-env-value",
      detail: "CLERK_SECRET_KEY=sk_l***",
    });
  });

  it("ignores unset and too-short secret values instead of matching everything", () => {
    const findings = findSecretsInBundle([file("abc 12345 true")], {
      GEMINI_API_KEY: undefined,
      DATABASE_URL: "",
      OAUTH_STATE_SECRET: "abc",
    });
    expect(findings).toEqual([]);
  });

  it("reports every file that leaks, not just the first", () => {
    const findings = findSecretsInBundle([
      file("process.env.NEXT_PUBLIC_STRIPE_SECRET_KEY", "a.js"),
      file("nothing here", "b.js"),
      file(`x="${fake("whsec_", "abcdefghijklmnop")}"`, "c.js"),
    ]);
    expect(findings.map((f) => f.path)).toEqual(["a.js", "c.js"]);
  });
});
