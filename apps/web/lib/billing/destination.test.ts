import { describe, expect, it } from "vitest";

import type { MeRead, SubscriptionRead } from "@/lib/api/client";
import { destinationFor } from "./destination";
import { daysUntil, formatPrice } from "./format";

const BUSINESS = {
  id: "b1",
  name: "Alpha Salon",
  industry: "Salon",
  address: "1 Main St",
  timezone: "America/New_York",
  phone: null,
  contact_email: null,
  currency: "USD",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

function access(entitled: boolean, status: string | null): SubscriptionRead {
  return {
    entitled,
    status,
    trial_ends_at: null,
    current_period_end: null,
    cancel_at_period_end: false,
    trial_available: !entitled,
  };
}

function me(overrides: Partial<MeRead>): MeRead {
  return { id: "u1", email: "o@example.com", business: BUSINESS, subscription: null, ...overrides };
}

describe("destinationFor", () => {
  it("sends an owner without a business to onboarding", () => {
    expect(destinationFor(me({ business: null }))).toBe("/onboarding");
  });

  it("sends an owner with no plan, a canceled plan or an open checkout to plan selection", () => {
    expect(destinationFor(me({ subscription: access(false, null) }))).toBe("/billing/plan");
    expect(destinationFor(me({ subscription: access(false, "canceled") }))).toBe("/billing/plan");
    expect(destinationFor(me({ subscription: null }))).toBe("/billing/plan");
  });

  it("lets an entitled owner into the dashboard", () => {
    expect(destinationFor(me({ subscription: access(true, "trialing") }))).toBe("/dashboard");
    expect(destinationFor(me({ subscription: access(true, "past_due") }))).toBe("/dashboard");
  });
});

describe("billing formatting", () => {
  it("formats whole and fractional prices", () => {
    expect(formatPrice(2900, "usd")).toBe("$29");
    expect(formatPrice(2950, "usd")).toBe("$29.50");
  });

  it("rounds days left up and never goes negative", () => {
    const now = new Date("2026-10-20T12:00:00Z");
    expect(daysUntil("2026-11-03T00:00:00Z", now)).toBe(14);
    expect(daysUntil("2026-10-01T00:00:00Z", now)).toBe(0);
  });
});
