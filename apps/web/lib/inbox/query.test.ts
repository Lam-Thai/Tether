import { describe, expect, it } from "vitest";

import { inboxHref, interactionHref, parseInboxQuery, withFilters } from "./query";

describe("parseInboxQuery", () => {
  it("reads known params", () => {
    expect(
      parseInboxQuery({
        type: "failed",
        status: "failed",
        intent: "BOOK_APPOINTMENT",
        from: "2026-10-01",
        to: "2026-10-31",
        cursor: "abc_-123",
      }),
    ).toEqual({
      type: "failed",
      status: "failed",
      intent: "BOOK_APPOINTMENT",
      from: "2026-10-01",
      to: "2026-10-31",
      cursor: "abc_-123",
    });
  });

  it("drops unknown types, malformed dates and odd values", () => {
    expect(
      parseInboxQuery({ type: "everything", from: "yesterday", to: "2026-13-45x", status: "a b", intent: "<x>" }),
    ).toEqual({});
  });

  it("takes the first of repeated params and ignores empty ones", () => {
    expect(parseInboxQuery({ type: ["bookings", "faqs"], from: "" })).toEqual({ type: "bookings" });
  });
});

describe("hrefs", () => {
  it("builds the inbox href without defaults", () => {
    expect(inboxHref({})).toBe("/dashboard/inbox");
    expect(inboxHref({ type: "all" })).toBe("/dashboard/inbox");
    expect(inboxHref({ type: "bookings", from: "2026-10-01" })).toBe(
      "/dashboard/inbox?type=bookings&from=2026-10-01",
    );
  });

  it("keeps the cursor only when asked", () => {
    expect(inboxHref({ type: "faqs", cursor: "c1" })).toBe("/dashboard/inbox?type=faqs&cursor=c1");
  });

  it("changing a filter drops the cursor", () => {
    expect(withFilters({ type: "faqs", cursor: "c1", from: "2026-10-01" }, { type: "failed" })).toEqual({
      type: "failed",
      from: "2026-10-01",
    });
    expect(withFilters({ cursor: "c1" }, { from: "2026-10-02", to: "" })).toEqual({ from: "2026-10-02" });
  });

  it("links a detail page with the inbox query (cursor included) to come back to", () => {
    expect(interactionHref("abc", { type: "failed", cursor: "c1" })).toBe(
      "/dashboard/inbox/abc?type=failed&cursor=c1",
    );
    expect(interactionHref("abc", {})).toBe("/dashboard/inbox/abc");
  });
});
