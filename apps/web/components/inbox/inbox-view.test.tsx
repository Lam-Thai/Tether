import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import type { InboxQuery, InteractionList, InteractionListResult, InteractionRow } from "@/lib/api/client";
import { InboxView } from "./inbox-view";

const row = (over: Partial<InteractionRow> = {}): InteractionRow => ({
  id: "id-1",
  channel: "test_chat",
  started_at: "2026-10-21T18:30:00Z",
  ended_at: "2026-10-21T18:34:00Z",
  caller: null,
  intent: "BOOK_APPOINTMENT",
  summary: "The customer asked to book a haircut. The appointment was booked for Fri 30 Oct at 3:30 PM.",
  summary_source: "ai",
  status: "completed",
  outcome: "booked",
  requires_human: false,
  needs_attention: false,
  escalation: null,
  ...over,
});

const ok = (items: InteractionRow[], over: Partial<InteractionList> = {}): InteractionListResult => ({
  kind: "ok",
  data: { items, next_cursor: null, timezone: "America/New_York", ...over },
});

const show = (result: InteractionListResult, query: InboxQuery = {}) =>
  render(<InboxView result={result} query={query} />);

const rowItems = () => within(screen.getByRole("list", { name: "Conversations" })).getAllByRole("listitem");

beforeEach(() => push.mockReset());

describe("InboxView rows", () => {
  it("shows time in the business timezone, caller, intent, summary and outcome", () => {
    show(ok([row()]));
    const item = rowItems()[0]!;
    expect(within(item).getByText("Oct 21, 2:30 PM")).toBeInTheDocument();
    expect(within(item).getByText("Test chat")).toBeInTheDocument();
    expect(within(item).getByText("Booking")).toBeInTheDocument();
    expect(within(item).getByText(/The customer asked to book a haircut/)).toBeInTheDocument();
    expect(within(item).getByText("Booked")).toBeInTheDocument();
    expect(screen.getByText("America/New_York")).toBeInTheDocument();
    expect(within(item).getByRole("link", { name: /open conversation/ })).toHaveAttribute(
      "href",
      "/dashboard/inbox/id-1",
    );
  });

  it("carries the inbox query on the detail link", () => {
    show(ok([row()]), { type: "failed", cursor: "c1" });
    expect(screen.getByRole("link", { name: /open conversation/ })).toHaveAttribute(
      "href",
      "/dashboard/inbox/id-1?type=failed&cursor=c1",
    );
  });

  it("falls back to Unknown caller and Summary pending", () => {
    show(ok([row({ channel: "voice", summary: null, summary_source: null, status: "in_progress", outcome: null })]));
    expect(screen.getByText("Unknown caller")).toBeInTheDocument();
    expect(screen.getByText("Summary pending")).toBeInTheDocument();
  });

  it("names a real caller", () => {
    show(ok([row({ caller: "Maya Chen" })]));
    expect(screen.getByText("Maya Chen")).toBeInTheDocument();
  });

  it("marks failed, in-progress and abandoned with text, not color alone", () => {
    show(
      ok([
        row({ id: "a", status: "failed", outcome: "failed" }),
        row({ id: "b", status: "in_progress", outcome: null }),
        row({ id: "c", status: "abandoned", outcome: "abandoned" }),
        row({ id: "d", status: "failed", outcome: null }),
      ]),
    );
    const items = rowItems();
    expect(within(items[0]!).getAllByText("Failed")).toHaveLength(1);
    expect(within(items[1]!).getByText("In progress")).toBeInTheDocument();
    expect(within(items[2]!).getAllByText("Abandoned")).toHaveLength(1);
    expect(within(items[3]!).getByText("Failed")).toBeInTheDocument();
  });

  it("shows the legacy badge for requires_human with no escalation record", () => {
    show(ok([row({ outcome: "escalated", requires_human: true })]));
    expect(screen.getByText("Escalated")).toBeInTheDocument();
    expect(screen.getByText("Needs a person")).toBeInTheDocument();
  });

  it("shows Needs attention with the reason, replacing the legacy badge", () => {
    show(
      ok([
        row({
          outcome: "escalated",
          requires_human: true,
          needs_attention: true,
          escalation: { id: "e1", reason: "booking_unresolved", urgency: "normal", status: "open", sms_status: "sent" },
        }),
      ]),
    );
    const badge = within(rowItems()[0]!).getByText(/Needs attention/);
    expect(badge).toHaveTextContent("Needs attention·Booking failed");
    expect(screen.queryByText("Needs a person")).not.toBeInTheDocument();
  });

  it("shows no attention mark once the escalation is resolved", () => {
    show(
      ok([
        row({
          requires_human: true,
          escalation: { id: "e1", reason: "complaint", urgency: "normal", status: "resolved", sms_status: "sent" },
        }),
      ]),
    );
    expect(within(rowItems()[0]!).queryByText(/Needs attention/)).not.toBeInTheDocument();
    expect(screen.queryByText("Needs a person")).not.toBeInTheDocument();
  });

  it("has a Needs attention filter chip that links to type=attention", () => {
    show(ok([row()]), { type: "attention" });
    const link = screen.getByRole("link", { name: "Needs attention" });
    expect(link).toHaveAttribute("aria-current", "page");
    expect(link).toHaveAttribute("href", "/dashboard/inbox?type=attention");
  });
});

describe("InboxView filters and paging", () => {
  it("marks the active tab and links tabs with the cursor dropped", () => {
    show(ok([row()]), { type: "faqs", from: "2026-10-01", cursor: "c1" });
    const tabs = within(screen.getByRole("navigation", { name: "Filter by type" }));
    expect(tabs.getByRole("link", { name: "FAQs" })).toHaveAttribute("aria-current", "page");
    expect(tabs.getByRole("link", { name: "All" })).not.toHaveAttribute("aria-current");
    expect(tabs.getByRole("link", { name: "Failed" })).toHaveAttribute(
      "href",
      "/dashboard/inbox?type=failed&from=2026-10-01",
    );
    expect(tabs.getByRole("link", { name: "All" })).toHaveAttribute("href", "/dashboard/inbox?from=2026-10-01");
  });

  it("treats a missing type as All", () => {
    show(ok([row()]));
    expect(screen.getByRole("link", { name: "All" })).toHaveAttribute("aria-current", "page");
  });

  it("pushes the date range without the cursor", async () => {
    const user = userEvent.setup();
    show(ok([row()]), { type: "bookings", cursor: "c1" });
    await user.type(screen.getByLabelText("From"), "2026-10-01");
    await user.type(screen.getByLabelText("To"), "2026-10-31");
    await user.click(screen.getByRole("button", { name: "Apply dates" }));
    expect(push).toHaveBeenCalledWith("/dashboard/inbox?type=bookings&from=2026-10-01&to=2026-10-31");
  });

  it("prefills the dates and offers Clear dates", () => {
    show(ok([row()]), { from: "2026-10-01", to: "2026-10-31" });
    expect(screen.getByLabelText("From")).toHaveValue("2026-10-01");
    expect(screen.getByLabelText("To")).toHaveValue("2026-10-31");
    expect(screen.getByRole("link", { name: "Clear dates" })).toHaveAttribute("href", "/dashboard/inbox");
  });

  it("offers Older from next_cursor, and Back to newest when on a later page", () => {
    show(ok([row()], { next_cursor: "n2" }), { type: "failed", cursor: "c1" });
    expect(screen.getByRole("link", { name: "Older" })).toHaveAttribute(
      "href",
      "/dashboard/inbox?type=failed&cursor=n2",
    );
    expect(screen.getByRole("link", { name: "Back to newest" })).toHaveAttribute(
      "href",
      "/dashboard/inbox?type=failed",
    );
  });

  it("offers no paging on a single first page", () => {
    show(ok([row()]));
    expect(screen.queryByRole("link", { name: "Older" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Back to newest" })).not.toBeInTheDocument();
  });
});

describe("InboxView states", () => {
  it("empty with no filters says no conversations yet", () => {
    show(ok([]));
    expect(screen.getByRole("heading", { name: "No conversations yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the test chat" })).toHaveAttribute("href", "/dashboard/agent");
  });

  it("empty with filters says no results for these filters", () => {
    show(ok([]), { type: "failed" });
    expect(screen.getByRole("heading", { name: "No results for these filters" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear filters" })).toHaveAttribute("href", "/dashboard/inbox");
    expect(screen.queryByText("No conversations yet")).not.toBeInTheDocument();
  });

  it("unreachable shows the error and still lets the filters be changed", () => {
    show({ kind: "unreachable", reason: "Could not reach the API." }, { type: "faqs" });
    expect(screen.getByRole("heading", { name: "Can't load your inbox" })).toBeInTheDocument();
    expect(screen.getByText(/Could not reach the API/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All" })).toBeInTheDocument();
  });

  it("validation shows the API message", () => {
    show({ kind: "validation", message: "from must be on or before to" }, { from: "2026-10-31", to: "2026-10-01" });
    expect(screen.getByText("from must be on or before to")).toBeInTheDocument();
  });

  it("unauthorized and subscription_required have their own panels", () => {
    const { unmount } = show({ kind: "unauthorized" });
    expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/sign-in");
    unmount();
    show({ kind: "subscription_required" });
    expect(screen.getByRole("link", { name: "Choose plan" })).toHaveAttribute("href", "/billing/plan");
  });
});
