import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { isProtectedRoute } from "./routes";

const request = (path: string) => new NextRequest(`http://localhost${path}`);

describe("isProtectedRoute", () => {
  it.each(["/", "/privacy", "/terms", "/privacy#google-user-data", "/status", "/sign-in", "/sign-up", "/sso-callback"])(
    "leaves %s public",
    (path) => {
      expect(isProtectedRoute(request(path))).toBe(false);
    },
  );

  it.each(["/dashboard", "/dashboard/settings/data", "/onboarding", "/billing/plan"])(
    "protects %s",
    (path) => {
      expect(isProtectedRoute(request(path))).toBe(true);
    },
  );
});
