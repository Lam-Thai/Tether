import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { SettingsNav } from "./settings-nav";

describe("SettingsNav", () => {
  it("links Integrations, Usage and Data and marks the current page", () => {
    render(<SettingsNav current="usage" />);
    expect(screen.getByRole("link", { name: "Integrations" })).toHaveAttribute(
      "href",
      "/dashboard/settings/integrations",
    );
    expect(screen.getByRole("link", { name: "Usage" })).toHaveAttribute("href", "/dashboard/settings/usage");
    expect(screen.getByRole("link", { name: "Data" })).toHaveAttribute("href", "/dashboard/settings/data");
    expect(screen.getByRole("link", { name: "Usage" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Data" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Integrations" })).not.toHaveAttribute("aria-current");
  });
});
