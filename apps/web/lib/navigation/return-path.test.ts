import { describe, expect, it } from "vitest";

import {
  backTarget,
  isReturnablePath,
  readReturnPath,
  rememberPath,
  RETURN_PATH_KEY,
} from "@/lib/navigation/return-path";

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    data,
  };
}

const throwing = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
};

describe("isReturnablePath", () => {
  it.each(["/", "/dashboard", "/dashboard/inbox?type=attention", "/sign-up", "/billing/plan"])(
    "accepts %s",
    (path) => {
      expect(isReturnablePath(path)).toBe(true);
    },
  );

  it.each([
    ["the legal pages", "/privacy"],
    ["the legal pages with an anchor", "/terms#usage-limits"],
    ["OAuth callbacks", "/sso-callback"],
    ["the single-use calendar callback", "/dashboard/settings/calendar/callback?code=x&state=y"],
    ["the checkout return", "/billing/return?session_id=cs_test"],
    ["another origin", "https://example.com/"],
    ["a protocol-relative URL", "//example.com/"],
    ["a backslash trick", "/\\example.com"],
    ["a control character", "/dash\nboard"],
    ["an empty string", ""],
  ])("rejects %s", (_, path) => {
    expect(isReturnablePath(path)).toBe(false);
  });
});

describe("rememberPath / readReturnPath", () => {
  it("stores a returnable path and reads it back", () => {
    const storage = memoryStorage();
    rememberPath(storage, "/dashboard/inbox?type=attention");
    expect(readReturnPath(storage)).toBe("/dashboard/inbox?type=attention");
  });

  it("keeps the last real page while the visitor reads the legal pages", () => {
    const storage = memoryStorage();
    rememberPath(storage, "/dashboard");
    rememberPath(storage, "/privacy");
    rememberPath(storage, "/terms");
    expect(readReturnPath(storage)).toBe("/dashboard");
  });

  it("ignores a stored value that is not returnable", () => {
    expect(readReturnPath(memoryStorage({ [RETURN_PATH_KEY]: "https://evil.example" }))).toBeNull();
  });

  it("survives storage that throws (private mode, blocked site data)", () => {
    expect(() => rememberPath(throwing, "/dashboard")).not.toThrow();
    expect(readReturnPath(throwing)).toBeNull();
  });
});

describe("backTarget", () => {
  it.each([
    [null, "/", "Back to home"],
    ["/", "/", "Back to home"],
    ["/dashboard", "/dashboard", "Back to dashboard"],
    ["/dashboard/settings/data", "/dashboard/settings/data", "Back to dashboard"],
    ["/sign-up", "/sign-up", "Back to sign-up"],
    ["/sign-in", "/sign-in", "Back to sign-in"],
    ["/onboarding", "/onboarding", "Back to setup"],
    ["/billing/plan", "/billing/plan", "Back to billing"],
    ["/status", "/status", "Back"],
  ])("%s goes to %s as %s", (stored, href, label) => {
    expect(backTarget(stored)).toEqual({ href, label });
  });
});
