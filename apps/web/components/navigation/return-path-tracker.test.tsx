import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReturnPathTracker } from "@/components/navigation/return-path-tracker";
import { RETURN_PATH_KEY } from "@/lib/navigation/return-path";

const nav = vi.hoisted(() => ({ pathname: "/", search: "" }));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
}));

describe("ReturnPathTracker", () => {
  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("remembers the current page with its query", () => {
    nav.pathname = "/dashboard/inbox";
    nav.search = "type=attention";
    render(<ReturnPathTracker />);
    expect(window.sessionStorage.getItem(RETURN_PATH_KEY)).toBe("/dashboard/inbox?type=attention");
  });

  it("keeps the previous page while a legal page is open", () => {
    window.sessionStorage.setItem(RETURN_PATH_KEY, "/");
    nav.pathname = "/privacy";
    nav.search = "";
    render(<ReturnPathTracker />);
    expect(window.sessionStorage.getItem(RETURN_PATH_KEY)).toBe("/");
  });
});
