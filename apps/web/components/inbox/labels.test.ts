import { describe, expect, it } from "vitest";

import type { InteractionRow } from "@/lib/api/client";
import { callerLabel, formatBusinessTime, intentLabel, outcomeLabel, statusLabel } from "./labels";

describe("formatBusinessTime", () => {
  it("formats in the business timezone, fixed en-US, not the viewer's", () => {
    // 18:30 UTC is 2:30 PM in New York (EDT) and 11:30 AM in Los Angeles.
    expect(formatBusinessTime("2026-10-21T18:30:00Z", "America/New_York")).toBe("Oct 21, 2:30 PM");
    expect(formatBusinessTime("2026-10-21T18:30:00Z", "America/Los_Angeles")).toBe("Oct 21, 11:30 AM");
  });

  it("can include the zone name and year", () => {
    expect(formatBusinessTime("2026-10-21T18:30:00Z", "America/New_York", { long: true })).toBe(
      "Oct 21, 2026, 2:30 PM EDT",
    );
  });

  it("falls back to UTC for an unknown zone and to the raw text for a bad date", () => {
    expect(formatBusinessTime("2026-10-21T18:30:00Z", "Not/AZone")).toBe("Oct 21, 6:30 PM");
    expect(formatBusinessTime("garbage", "UTC")).toBe("garbage");
  });
});

describe("labels", () => {
  const row = (over: Partial<InteractionRow>) => ({ channel: "test_chat", caller: null, ...over }) as InteractionRow;

  it("names the caller", () => {
    expect(callerLabel(row({}))).toBe("Test chat");
    expect(callerLabel(row({ caller: "Maya" }))).toBe("Maya");
    expect(callerLabel(row({ channel: "voice" }))).toBe("Unknown caller");
  });

  it("labels known intents and derives the rest", () => {
    expect(intentLabel("BOOK_APPOINTMENT")).toBe("Booking");
    expect(intentLabel("FAQ")).toBe("FAQ");
    expect(intentLabel("SOMETHING_NEW")).toBe("Something new");
    expect(intentLabel(null)).toBe("No intent yet");
  });

  it("labels outcomes and statuses", () => {
    expect(outcomeLabel("booked")).toBe("Booked");
    expect(outcomeLabel("escalated")).toBe("Escalated");
    expect(statusLabel("in_progress")).toBe("In progress");
    expect(statusLabel("abandoned")).toBe("Abandoned");
  });
});
