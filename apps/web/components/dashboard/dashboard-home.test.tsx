import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import type {
  BusinessRead,
  DashboardSummaryResult,
  IntegrationStatusResult,
  InteractionListResult,
} from "@/lib/api/client";
import { DashboardHome } from "./dashboard-home";
import { integrationStatus, readiness, row, SUMMARY } from "./test-fixtures";

const BUSINESS: BusinessRead = {
  id: "b-1",
  name: "Harbor Salon",
  industry: "Hair salon",
  address: "1 Main St",
  timezone: "America/New_York",
  phone: null,
  contact_email: null,
  currency: "USD",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

const okSummary: DashboardSummaryResult = { kind: "ok", data: SUMMARY };
const okStatus = (over: Parameters<typeof integrationStatus>[0] = {}): IntegrationStatusResult => ({
  kind: "ok",
  data: integrationStatus(over),
});
const okRecent = (items = [row()]): InteractionListResult => ({
  kind: "ok",
  data: { items, next_cursor: null, timezone: "America/New_York" },
});
const failed = { kind: "unreachable", reason: "Could not reach the API." } as const;

function show(
  over: Partial<{
    summary: DashboardSummaryResult;
    integrations: IntegrationStatusResult;
    recent: InteractionListResult;
  }> = {},
) {
  return render(
    <DashboardHome
      business={{ kind: "ok", data: BUSINESS }}
      summary={over.summary ?? okSummary}
      integrations={over.integrations ?? okStatus()}
      recent={over.recent ?? okRecent()}
      timeZone="America/New_York"
    />,
  );
}

describe("DashboardHome: stat tiles", () => {
  it("shows the four numbers with the range label and links Needs attention to the inbox", () => {
    show();
    const tiles = screen.getByRole("region", { name: "Summary" });
    expect(within(tiles).getByText("Last 30 days")).toBeInTheDocument();
    expect(within(tiles).getByText("Sep 29 to Oct 28, 2026")).toBeInTheDocument();
    expect(within(tiles).getByText("America/New_York")).toBeInTheDocument();
    for (const [label, value] of [
      ["Total interactions", "12"],
      ["Successful bookings", "3"],
      ["FAQs answered", "5"],
      ["Needs attention", "2"],
    ] as const) {
      const term = within(tiles).getByText(label);
      expect(term.closest("dt")?.nextElementSibling).toHaveTextContent(value);
    }
    expect(within(tiles).getByRole("link", { name: "Review in the inbox" })).toHaveAttribute(
      "href",
      "/dashboard/inbox?type=attention",
    );
  });

  it("reads a zero queue as nothing waiting", () => {
    show({ summary: { kind: "ok", data: { ...SUMMARY, open_escalations: 0, total_interactions: 0 } } });
    expect(screen.getByText("Nothing waiting for you.")).toBeInTheDocument();
    expect(screen.getByText("None in this range yet.")).toBeInTheDocument();
  });

  it("fails alone: a Callout in place of the tiles while the other sections still render", () => {
    show({ summary: failed });
    expect(screen.getByRole("heading", { name: "Can't load your numbers" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Summary" })).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Recent conversations" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Integration status" })).toBeInTheDocument();
  });

  it("shows a validation message from the API", () => {
    show({ summary: { kind: "validation", message: "From must not be after to." } });
    expect(screen.getByText("From must not be after to.")).toBeInTheDocument();
  });

  it("points to the plan page when the plan is required", () => {
    show({ summary: { kind: "subscription_required" } });
    expect(screen.getByRole("link", { name: "Choose plan" })).toHaveAttribute("href", "/billing/plan");
  });
});

describe("DashboardHome: recent conversations", () => {
  it("lists rows in the business timezone, each linking to its inbox detail", () => {
    show({ recent: okRecent([row(), row({ id: "id-2", caller: "+15557654321", outcome: "answered", intent: "FAQ" })]) });
    const list = screen.getByRole("list", { name: "Recent conversations" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]!).getByText("Oct 21, 2:30 PM")).toBeInTheDocument();
    expect(within(items[0]!).getByRole("link", { name: /open conversation/ })).toHaveAttribute(
      "href",
      "/dashboard/inbox/id-1",
    );
    expect(within(items[1]!).getByText("+15557654321")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View all conversations" })).toHaveAttribute("href", "/dashboard/inbox");
  });

  it("marks an open escalation on its row", () => {
    show({ recent: okRecent([row({ needs_attention: true, escalation: null })]) });
    const item = within(screen.getByRole("list", { name: "Recent conversations" })).getAllByRole("listitem")[0]!;
    expect(within(item).getByText("Needs attention")).toBeInTheDocument();
  });

  it("has an empty state that points at the test chat", () => {
    show({ recent: okRecent([]), integrations: okStatus() });
    expect(screen.getByRole("heading", { name: "No conversations yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the test chat" })).toHaveAttribute(
      "href",
      "/dashboard/agent#test-chat",
    );
  });

  it("fails alone with a Callout", () => {
    show({ recent: failed });
    expect(screen.getByRole("heading", { name: "Can't load recent conversations" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Summary" })).toBeInTheDocument();
  });
});

describe("DashboardHome: integrations", () => {
  it("reads a lost calendar connection as Reconnect needed, with a reconnect link and the error", () => {
    show({
      integrations: okStatus({
        calendar: {
          configured: true,
          status: "reconnect_needed",
          calendar_name: "Salon",
          last_error: "auth_revoked",
          last_synced_at: null,
        },
        errors: [{ source: "calendar", code: "auth_revoked", occurred_at: "2026-10-28T13:30:00Z" }],
      }),
    });
    const list = screen.getByRole("list", { name: "Integration status" });
    expect(within(list).getByText("Reconnect needed")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Reconnect calendar" })).toHaveAttribute(
      "href",
      "/dashboard/settings/calendar",
    );
    const errors = screen.getByRole("heading", { name: "Recent integration errors" }).closest("[data-slot=card]")!;
    expect(within(errors as HTMLElement).getByText("Google Calendar")).toBeInTheDocument();
    expect(within(errors as HTMLElement).getByText(/stopped accepting Tether's access/)).toBeInTheDocument();
    expect(within(errors as HTMLElement).getByText("auth_revoked")).toBeInTheDocument();
    expect(within(errors as HTMLElement).getByText("Oct 28, 9:30 AM")).toBeInTheDocument();
    expect(within(errors as HTMLElement).getByRole("link", { name: "Fix Google Calendar problem" })).toHaveAttribute(
      "href",
      "/dashboard/settings/calendar",
    );
  });

  it("shows a connected calendar and phone with their details", () => {
    show();
    const list = screen.getByRole("list", { name: "Integration status" });
    expect(within(list).getAllByText("Connected")).toHaveLength(2);
    expect(within(list).getByText("Salon")).toBeInTheDocument();
    expect(within(list).getByText("+1 555 123 4567")).toBeInTheDocument();
    expect(within(list).getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("No integration errors.")).toBeInTheDocument();
  });

  it("shows unconfigured and not-connected states with plain copy", () => {
    show({
      integrations: okStatus({
        calendar: { configured: false, status: "not_connected", calendar_name: null, last_error: null, last_synced_at: null },
        phone: { configured: true, status: "not_connected", phone_number: null, last_error: null, verified_at: null },
      }),
    });
    expect(screen.getByText("Not available")).toBeInTheDocument();
    expect(screen.getByText(/Google Calendar isn't set up on this server yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a number" })).toHaveAttribute("href", "/dashboard/phone");
  });

  it("derives a label for a status it does not know instead of crashing", () => {
    show({
      integrations: okStatus({
        calendar: { configured: true, status: "paused_by_provider", calendar_name: null, last_error: null, last_synced_at: null },
        phone: { configured: true, status: "porting", phone_number: null, last_error: null, verified_at: null },
        errors: [
          { source: "whatsapp", code: "template_rejected", occurred_at: null },
          { source: "sms", code: "30003", occurred_at: "2026-10-28T10:00:00Z" },
        ],
      }),
    });
    expect(screen.getByText("Paused by provider")).toBeInTheDocument();
    expect(screen.getByText("Porting")).toBeInTheDocument();
    expect(screen.getByText("Whatsapp")).toBeInTheDocument();
    expect(screen.getByText("Template rejected.")).toBeInTheDocument();
    expect(screen.getByText("A text alert was not delivered.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fix Whatsapp problem" })).toHaveAttribute(
      "href",
      "/dashboard/settings/integrations",
    );
    expect(screen.getByRole("link", { name: "Fix Text alerts problem" })).toHaveAttribute(
      "href",
      "/dashboard/agent#escalation",
    );
  });

  it("shows usage against the limits and flags a meter at its limit", () => {
    show();
    const meters = screen.getAllByRole("meter");
    expect(meters.map((m) => m.getAttribute("aria-label"))).toEqual(["AI replies", "Text messages"]);
    expect(meters[1]).toHaveAttribute("aria-valuetext", "10 of 10 used, limit reached");
    expect(screen.getByText("Limit reached")).toBeInTheDocument();
  });

  it("fails alone with a Callout while tiles and conversations still render", () => {
    show({ integrations: failed });
    expect(screen.getByRole("heading", { name: "Can't load integration status" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Integration status" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Summary" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Recent conversations" })).toBeInTheDocument();
  });
});

describe("DashboardHome: Get set up guide", () => {
  const inactive = (failing: string[]) =>
    okStatus({
      agent: { active: false, ready: failing.length === 0 },
      readiness: readiness(failing),
    });

  it("shows for a new owner (no conversations, agent inactive) with a fix link per open step", () => {
    show({ recent: okRecent([]), integrations: inactive(["services", "calendar"]) });
    expect(screen.getByRole("heading", { name: "Get set up" })).toBeInTheDocument();
    expect(screen.getByText("2 steps left before your receptionist can answer callers.")).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Setup steps" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(7);
    expect(within(steps).getByRole("link", { name: "Fix Services" })).toHaveAttribute(
      "href",
      "/dashboard/agent#services",
    );
    expect(within(steps).getByRole("link", { name: "Fix Google Calendar" })).toHaveAttribute(
      "href",
      "/dashboard/settings/calendar",
    );
    expect(within(steps).getAllByRole("link")).toHaveLength(2);
  });

  it("offers activation when every step is done", () => {
    show({ recent: okRecent([]), integrations: inactive([]) });
    expect(screen.getByText(/Everything is in place/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to activation" })).toHaveAttribute(
      "href",
      "/dashboard/agent#activation",
    );
  });

  it("does not show once there are conversations", () => {
    show({ recent: okRecent([row()]), integrations: inactive(["services"]) });
    expect(screen.queryByRole("heading", { name: "Get set up" })).not.toBeInTheDocument();
  });

  it("does not show once the agent is active", () => {
    show({ recent: okRecent([]), integrations: okStatus() });
    expect(screen.queryByRole("heading", { name: "Get set up" })).not.toBeInTheDocument();
  });

  it("does not guess when either fetch failed", () => {
    show({ recent: failed, integrations: inactive(["services"]) });
    expect(screen.queryByRole("heading", { name: "Get set up" })).not.toBeInTheDocument();
    show({ recent: okRecent([]), integrations: failed });
    expect(screen.queryByRole("heading", { name: "Get set up" })).not.toBeInTheDocument();
  });
});
