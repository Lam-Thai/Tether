import { describe, expect, it } from "vitest";

import {
  calendarStatusView,
  deriveLabel,
  describeIntegrationError,
  phoneStatusView,
  rangeSummary,
  readinessLabel,
} from "./integration-labels";

describe("integration labels", () => {
  it("reads reconnect_needed as Reconnect needed", () => {
    expect(calendarStatusView("reconnect_needed", true)).toEqual({ tone: "warning", label: "Reconnect needed" });
  });

  it("derives a neutral label for a status it does not know", () => {
    expect(calendarStatusView("paused_by_provider", true)).toEqual({ tone: "neutral", label: "Paused by provider" });
    expect(phoneStatusView("porting", true)).toEqual({ tone: "neutral", label: "Porting" });
    expect(phoneStatusView("", true).label).toBe("Unknown");
  });

  it("calls an unconfigured, unconnected integration Not available", () => {
    expect(calendarStatusView("not_connected", false).label).toBe("Not available");
    expect(phoneStatusView("not_connected", false).label).toBe("Not available");
    expect(phoneStatusView("not_connected", true).label).toBe("Not connected");
  });

  it("describes known error codes and falls back to the code for unknown ones", () => {
    expect(describeIntegrationError({ source: "calendar", code: "auth_revoked" }).message).toMatch(/stopped accepting/);
    expect(describeIntegrationError({ source: "sms", code: "sms_failed" }).message).toBe("A text alert could not be sent.");
    expect(describeIntegrationError({ source: "sms", code: "30003" }).message).toBe("A text alert was not delivered.");
    expect(describeIntegrationError({ source: "fax", code: "line_busy" })).toMatchObject({
      source: "Fax",
      message: "Line busy.",
      code: "line_busy",
      fixUrl: "/dashboard/settings/integrations",
    });
    expect(describeIntegrationError({ source: "calendar", code: "" }).message).toBe("Unknown problem.");
  });

  it("labels readiness keys, deriving one for a new key", () => {
    expect(readinessLabel("escalation_contact")).toBe("Escalation contact");
    expect(readinessLabel("brand_new_step")).toBe("Brand new step");
    expect(deriveLabel("", "Fallback")).toBe("Fallback");
  });

  it("summarizes the default 30-day range and other spans", () => {
    expect(rangeSummary("2026-09-29", "2026-10-28")).toEqual({ title: "Last 30 days", dates: "Sep 29 to Oct 28, 2026" });
    expect(rangeSummary("2026-10-28", "2026-10-28").title).toBe("1 day");
    expect(rangeSummary("2026-10-01", "2026-10-07").title).toBe("7 days");
    expect(rangeSummary("garbage", "2026-10-07").title).toBe("Selected range");
  });
});
