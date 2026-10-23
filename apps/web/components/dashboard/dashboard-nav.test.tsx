import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

let pathname = "/dashboard";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

import { DashboardNav } from "./dashboard-nav";

describe("DashboardNav", () => {
  it("links Agent, Calendar and Settings and leaves the other sections disabled", () => {
    pathname = "/dashboard";
    render(<DashboardNav />);

    const link = screen.getByRole("link", { name: "Agent" });
    expect(link).toHaveAttribute("href", "/dashboard/agent");
    expect(link).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/dashboard/settings",
    );
    expect(screen.getByRole("link", { name: "Calendar" })).toHaveAttribute(
      "href",
      "/dashboard/settings/calendar",
    );
    expect(screen.getAllByRole("link")).toHaveLength(3);

    for (const label of ["FAQs", "Services", "Inbox", "Phone"]) {
      expect(screen.getByText(label)).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("marks Agent as the current page on its route", () => {
    pathname = "/dashboard/agent";
    render(<DashboardNav />);
    expect(screen.getByRole("link", { name: "Agent" })).toHaveAttribute("aria-current", "page");
  });

  it("marks Settings as current on any settings route", () => {
    pathname = "/dashboard/settings/usage";
    render(<DashboardNav />);
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Agent" })).not.toHaveAttribute("aria-current");
  });

  it("marks Calendar, not Settings, as current on the calendar routes", () => {
    pathname = "/dashboard/settings/calendar/callback";
    render(<DashboardNav />);
    expect(screen.getByRole("link", { name: "Calendar" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Settings" })).not.toHaveAttribute("aria-current");
  });
});
