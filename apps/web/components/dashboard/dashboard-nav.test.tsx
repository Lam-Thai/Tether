import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

let pathname = "/dashboard";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

import { DashboardNav } from "./dashboard-nav";

describe("DashboardNav", () => {
  it("links Agent and Settings and leaves the other sections disabled", () => {
    pathname = "/dashboard";
    render(<DashboardNav />);

    const link = screen.getByRole("link", { name: "Agent" });
    expect(link).toHaveAttribute("href", "/dashboard/agent");
    expect(link).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/dashboard/settings",
    );
    expect(screen.getAllByRole("link")).toHaveLength(2);

    for (const label of ["FAQs", "Services", "Calendar", "Inbox", "Phone"]) {
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
});
