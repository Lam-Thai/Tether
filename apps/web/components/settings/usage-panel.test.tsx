import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { UsageMeter, UsageRead } from "@/lib/api/client";
import {
  formatEstimatedCost,
  GLOBAL_CONCURRENCY_MESSAGE,
  GLOBAL_LIMIT_MESSAGE,
  meterLabel,
  UsagePanel,
} from "./usage-panel";

const meter = (kind: string, used: number, limit: number, global = false): UsageMeter => ({
  kind,
  used,
  limit,
  global_limit_reached: global,
});

function usage(overrides: Partial<UsageRead> = {}): UsageRead {
  return {
    window: { timezone: "UTC", starts_at: "2026-10-21T00:00:00Z", resets_at: "2026-10-22T00:00:00Z" },
    meters: [
      meter("gemini_request", 3, 50),
      meter("tts_request", 0, 10),
      meter("call", 0, 10),
      meter("sms", 0, 10),
    ],
    gemini_tokens: 1834,
    failed_requests: 1,
    estimated_cost_usd: "0.004210",
    concurrency: { active: 1, limit: 3, idle_minutes: 15, global_limit_reached: false },
    max_call_duration_seconds: 600,
    ...overrides,
  };
}

describe("UsagePanel", () => {
  it("shows each meter with its label, used of limit, and an accessible meter", () => {
    render(<UsagePanel usage={{ kind: "ok", data: usage() }} />);

    expect(screen.getByRole("heading", { level: 1, name: "Usage" })).toBeInTheDocument();
    const ai = screen.getByRole("meter", { name: "AI replies" });
    expect(ai).toHaveAttribute("aria-valuenow", "3");
    expect(ai).toHaveAttribute("aria-valuemax", "50");
    expect(ai).toHaveAttribute("aria-valuetext", "3 of 50 used");
    for (const label of ["Voice replies", "Phone calls", "Text messages"]) {
      expect(screen.getByRole("meter", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByText("00:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("1,834")).toBeInTheDocument();
    expect(screen.getByText("$0.0042")).toBeInTheDocument();
    expect(screen.getByText("Estimate only, not a bill.")).toBeInTheDocument();
    expect(screen.getByText("1 of 3")).toBeInTheDocument();
    expect(screen.getByText("10 min")).toBeInTheDocument();
    expect(screen.queryByText(GLOBAL_LIMIT_MESSAGE)).not.toBeInTheDocument();
    expect(screen.queryByText("Limit reached")).not.toBeInTheDocument();
    expect(screen.queryByText(/No usage yet today/)).not.toBeInTheDocument();
  });

  it("warns when the platform-wide limit is reached", () => {
    render(
      <UsagePanel
        usage={{
          kind: "ok",
          data: usage({ meters: [meter("gemini_request", 3, 50, true), meter("sms", 0, 10)] }),
        }}
      />,
    );
    expect(screen.getByText(GLOBAL_LIMIT_MESSAGE)).toBeInTheDocument();
    expect(screen.getByText("Platform limit")).toBeInTheDocument();
    expect(screen.queryByText(GLOBAL_CONCURRENCY_MESSAGE)).not.toBeInTheDocument();
  });

  it("keeps the reply-fallback warning to the AI-reply meter", () => {
    render(
      <UsagePanel
        usage={{
          kind: "ok",
          data: usage({ meters: [meter("gemini_request", 3, 50), meter("sms", 0, 0, true)] }),
        }}
      />,
    );
    expect(screen.queryByText(GLOBAL_LIMIT_MESSAGE)).not.toBeInTheDocument();
    expect(screen.getByText("Max call length")).toBeInTheDocument();
  });

  it("explains a platform concurrency cap without claiming replies fall back", () => {
    render(
      <UsagePanel
        usage={{
          kind: "ok",
          data: usage({
            concurrency: { active: 0, limit: 3, idle_minutes: 15, global_limit_reached: true },
          }),
        }}
      />,
    );
    expect(screen.getByText(GLOBAL_CONCURRENCY_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByText(GLOBAL_LIMIT_MESSAGE)).not.toBeInTheDocument();
  });

  it("marks a meter whose business limit is reached, in text", () => {
    render(
      <UsagePanel
        usage={{ kind: "ok", data: usage({ meters: [meter("gemini_request", 50, 50)] }) }}
      />,
    );
    expect(screen.getByText("Limit reached")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "AI replies" })).toHaveAttribute("aria-valuenow", "50");
    expect(screen.queryByText(GLOBAL_LIMIT_MESSAGE)).not.toBeInTheDocument();
  });

  it("shows zeros and a plain sentence when nothing was used today", () => {
    render(
      <UsagePanel
        usage={{
          kind: "ok",
          data: usage({
            meters: [meter("gemini_request", 0, 50)],
            gemini_tokens: 0,
            failed_requests: 0,
            estimated_cost_usd: "0",
            concurrency: { active: 0, limit: 3, idle_minutes: 15, global_limit_reached: false },
          }),
        }}
      />,
    );
    expect(screen.getByText(/No usage yet today/)).toBeInTheDocument();
    expect(screen.getByText("$0.00")).toBeInTheDocument();
    expect(screen.getByText("0 of 3")).toBeInTheDocument();
  });

  it("renders an unknown meter kind with a generic label", () => {
    render(
      <UsagePanel
        usage={{ kind: "ok", data: usage({ meters: [meter("whatsapp_message", 1, 5)] }) }}
      />,
    );
    expect(screen.getByRole("meter", { name: "Whatsapp message" })).toBeInTheDocument();
  });

  it("shows an error callout when the API is unreachable", () => {
    render(<UsagePanel usage={{ kind: "unreachable", reason: "Could not reach the API." }} />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Can't load your usage" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Could not reach the API\./)).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("handles the backstop 401 and 402 results", () => {
    const { unmount } = render(<UsagePanel usage={{ kind: "unauthorized" }} />);
    expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/sign-in");
    unmount();
    render(<UsagePanel usage={{ kind: "subscription_required" }} />);
    expect(screen.getByRole("link", { name: "Choose plan" })).toHaveAttribute("href", "/billing/plan");
  });

  it("never uses dashes in its visible copy", () => {
    const { container } = render(
      <UsagePanel
        usage={{ kind: "ok", data: usage({ meters: [meter("gemini_request", 50, 50, true)] }) }}
      />,
    );
    expect(container.textContent).not.toMatch(/[–—]/);
  });
});

describe("helpers", () => {
  it("formats the estimated cost", () => {
    expect(formatEstimatedCost("0.004210")).toBe("$0.0042");
    expect(formatEstimatedCost("1.5")).toBe("$1.50");
    expect(formatEstimatedCost("0.000000")).toBe("$0.00");
  });

  it("labels kinds", () => {
    expect(meterLabel("call")).toBe("Phone calls");
    expect(meterLabel("calendar_call")).toBe("Calendar checks");
    expect(meterLabel("new_thing")).toBe("New thing");
    expect(meterLabel("")).toBe("Other usage");
  });
});
