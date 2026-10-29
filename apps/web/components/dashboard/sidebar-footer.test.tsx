import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

vi.mock("@clerk/nextjs", () => ({ useClerk: () => ({ signOut: vi.fn() }) }));

import { SidebarFooter } from "./sidebar-footer";

describe("SidebarFooter", () => {
  it("shows Sign out beside the Privacy and Terms links", () => {
    render(<SidebarFooter />);

    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    const legal = screen.getByRole("navigation", { name: "Legal" });
    expect(within(legal).getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "/privacy");
    expect(within(legal).getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/terms");
  });
});
