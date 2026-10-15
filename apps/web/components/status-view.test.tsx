import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusView } from "./status-view";
import type { HealthResult } from "@/lib/api/client";

describe("StatusView", () => {
  it("renders the healthy state", () => {
    const result: HealthResult = {
      kind: "healthy",
      data: { status: "ok", db: "connected", version: "1.2.3" },
    };

    render(<StatusView result={result} />);

    expect(screen.getByText("API: healthy · Database: connected")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("1.2.3")).toBeInTheDocument();
    expect(screen.queryByText(/can't reach the api/i)).not.toBeInTheDocument();
  });

  it("renders a distinct degraded state naming the database as the problem", () => {
    const result: HealthResult = {
      kind: "degraded",
      data: { status: "degraded", db: "error", version: "1.2.3" },
    };

    render(<StatusView result={result} />);

    expect(screen.getByText("Database unavailable")).toBeInTheDocument();
    expect(screen.getByText(/could not reach the database/i)).toBeInTheDocument();
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.queryByText("API: healthy · Database: connected")).not.toBeInTheDocument();
  });

  it("renders a distinct unreachable state when the API can't be reached", () => {
    const result: HealthResult = {
      kind: "unreachable",
      reason: "Could not reach the API. It may be down or unreachable from here.",
    };

    render(<StatusView result={result} />);

    expect(screen.getByText("Can't reach the API")).toBeInTheDocument();
    expect(
      screen.getByText("Could not reach the API. It may be down or unreachable from here."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Database unavailable")).not.toBeInTheDocument();
    expect(screen.queryByText("API: healthy · Database: connected")).not.toBeInTheDocument();
  });
});
