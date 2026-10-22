import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BusinessOverview } from "./business-overview";
import type { BusinessResult } from "@/lib/api/client";

const BUSINESS = {
  id: "b1",
  name: "Acme Dental",
  industry: "Dental",
  address: "123 Main St",
  timezone: "America/New_York",
  phone: "+15551234567",
  contact_email: "owner@acme.test",
  currency: "USD",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

describe("BusinessOverview", () => {
  it("renders the business details on the happy path", () => {
    const result: BusinessResult = { kind: "ok", data: BUSINESS };

    render(<BusinessOverview result={result} />);

    expect(screen.getByText("Acme Dental")).toBeInTheDocument();
    expect(screen.getByText("Dental")).toBeInTheDocument();
    expect(screen.getByText("123 Main St")).toBeInTheDocument();
    expect(screen.getByText("America/New_York")).toBeInTheDocument();
    expect(screen.getByText("+15551234567")).toBeInTheDocument();
    expect(screen.getByText("owner@acme.test")).toBeInTheDocument();
  });

  it("falls back to 'Not set' for missing optional fields", () => {
    const result: BusinessResult = {
      kind: "ok",
      data: { ...BUSINESS, phone: null, contact_email: null },
    };

    render(<BusinessOverview result={result} />);

    expect(screen.getAllByText("Not set")).toHaveLength(2);
  });

  it("renders an empty state when there's no business yet", () => {
    const result: BusinessResult = { kind: "not_found" };

    render(<BusinessOverview result={result} />);

    expect(screen.getByText("No business yet")).toBeInTheDocument();
    expect(screen.queryByText("Acme Dental")).not.toBeInTheDocument();
  });

  it("renders a distinct error state when the API is unreachable", () => {
    const result: BusinessResult = { kind: "unreachable", reason: "network down" };

    render(<BusinessOverview result={result} />);

    expect(screen.getByText("Can't load your business")).toBeInTheDocument();
    expect(screen.getByText("network down")).toBeInTheDocument();
  });

  it("renders a distinct error state when unauthorized", () => {
    const result: BusinessResult = { kind: "unauthorized" };

    render(<BusinessOverview result={result} />);

    expect(screen.getByText("Your session has expired")).toBeInTheDocument();
  });
});
