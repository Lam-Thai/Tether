import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import type { IntegrationStatusResult } from "@/lib/api/client";
import { integrationStatus, readiness } from "@/components/dashboard/test-fixtures";
import { IntegrationsPanel } from "./integrations-panel";

const ok = (over: Parameters<typeof integrationStatus>[0] = {}): IntegrationStatusResult => ({
  kind: "ok",
  data: integrationStatus(over),
});

const show = (result: IntegrationStatusResult) =>
  render(<IntegrationsPanel result={result} timeZone="America/New_York" />);

describe("IntegrationsPanel", () => {
  it("shows the page title, the settings nav and every integration with details", () => {
    show(ok());
    expect(screen.getByRole("heading", { level: 1, name: "Integrations" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Integrations" })).toHaveAttribute("aria-current", "page");
    const list = screen.getByRole("list", { name: "Integration status" });
    expect(within(list).getByRole("heading", { name: "Calendar" })).toBeInTheDocument();
    expect(within(list).getByRole("heading", { name: "Phone" })).toBeInTheDocument();
    expect(within(list).getByRole("heading", { name: "Receptionist" })).toBeInTheDocument();
    expect(within(list).getByRole("heading", { name: "Usage today" })).toBeInTheDocument();
    expect(within(list).getByText("Salon")).toBeInTheDocument();
    expect(within(list).getByText("Oct 28, 9:30 AM")).toBeInTheDocument();
    expect(within(list).getByText("Oct 28, 8:00 AM")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Activation checklist" })).not.toBeInTheDocument();
  });

  it("shows the activation checklist while the agent is not active", () => {
    show(
      ok({
        agent: { active: false, ready: false },
        readiness: readiness(["services"]),
      }),
    );
    const list = screen.getByRole("list", { name: "Activation checklist" });
    expect(within(list).getByRole("link", { name: "Fix Services" })).toHaveAttribute("href", "/dashboard/agent#services");
    expect(screen.getByRole("link", { name: "Open the Agent Builder" })).toHaveAttribute(
      "href",
      "/dashboard/agent#activation",
    );
    expect(screen.getByText("Inactive")).toBeInTheDocument();
  });

  it("links to activation when everything is ready but the agent is off", () => {
    show(ok({ agent: { active: false, ready: true }, readiness: readiness([]) }));
    expect(screen.getByRole("link", { name: "Go to activation" })).toBeInTheDocument();
  });

  it("fails with a Callout and no cards", () => {
    show({ kind: "unreachable", reason: "Could not reach the API." });
    expect(screen.getByRole("heading", { name: "Can't load integration status" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Integration status" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Integrations" })).toBeInTheDocument();
  });

  it("lists recent errors with a fix link and an empty state without them", () => {
    show(ok({ errors: [{ source: "phone", code: "voice_url_mismatch", occurred_at: "2026-10-28T13:00:00Z" }] }));
    expect(screen.getByText(/Voice URL no longer points at Tether/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fix Phone problem" })).toHaveAttribute("href", "/dashboard/phone");
  });
});
