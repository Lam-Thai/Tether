import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { PlanRead, SubscriptionRead } from "@/lib/api/client";

const replaceMock = vi.fn();
const getTokenMock = vi.fn();
const startCheckoutMock = vi.fn();
const syncCheckoutMock = vi.fn();
const openBillingPortalMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  startCheckout: (...args: unknown[]) => startCheckoutMock(...args),
  syncCheckout: (...args: unknown[]) => syncCheckoutMock(...args),
  openBillingPortal: (...args: unknown[]) => openBillingPortalMock(...args),
}));

import { BillingStatus } from "./billing-status";
import { CheckoutReturn } from "./checkout-return";
import { PlanChooser } from "./plan-chooser";

const PLAN: PlanRead = {
  amount: 2900,
  currency: "usd",
  interval: "month",
  trial_days: 14,
  trial_available: true,
};

function access(overrides: Partial<SubscriptionRead> = {}): SubscriptionRead {
  return {
    entitled: false,
    status: null,
    trial_ends_at: null,
    current_period_end: null,
    cancel_at_period_end: false,
    trial_available: true,
    ...overrides,
  };
}

const assignMock = vi.fn();
const originalLocation = window.location;

beforeEach(() => {
  replaceMock.mockReset();
  getTokenMock.mockReset().mockResolvedValue("token-123");
  startCheckoutMock.mockReset();
  syncCheckoutMock.mockReset();
  openBillingPortalMock.mockReset();
  assignMock.mockReset();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, assign: assignMock },
  });
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  vi.useRealTimers();
});

describe("PlanChooser", () => {
  it("shows both options with the Stripe price", () => {
    render(<PlanChooser plan={PLAN} canceled={false} />);

    expect(screen.getByRole("button", { name: "Start free trial" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Subscribe" })).toBeInTheDocument();
    expect(screen.getByText("14-day free trial")).toBeInTheDocument();
    expect(screen.getByText("No card required")).toBeInTheDocument();
    expect(screen.getByText("$29")).toBeInTheDocument();
  });

  it("asks the API for a trial checkout and redirects to Stripe", async () => {
    startCheckoutMock.mockResolvedValue({ kind: "ok", url: "https://checkout.stripe.com/c/1" });
    render(<PlanChooser plan={PLAN} canceled={false} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Start free trial" }));

    await waitFor(() => expect(assignMock).toHaveBeenCalledWith("https://checkout.stripe.com/c/1"));
    expect(startCheckoutMock).toHaveBeenCalledWith("token-123", "trial");
    expect(screen.getByRole("button", { name: "Opening checkout…" })).toBeDisabled();
  });

  it("hides the trial once it has been used", () => {
    render(<PlanChooser plan={{ ...PLAN, trial_available: false }} canceled={false} />);

    expect(screen.queryByRole("button", { name: "Start free trial" })).not.toBeInTheDocument();
    expect(screen.getByText("This business has already used its free trial.")).toBeInTheDocument();
  });

  it("says nothing was charged after Checkout is closed", () => {
    render(<PlanChooser plan={PLAN} canceled />);
    expect(screen.getByText("Checkout closed")).toBeInTheDocument();
  });

  it("explains a refused trial and lets the owner try again", async () => {
    startCheckoutMock.mockResolvedValue({ kind: "trial_already_used" });
    render(<PlanChooser plan={PLAN} canceled={false} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Start free trial" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already used its free trial");
    expect(screen.getByRole("button", { name: "Subscribe" })).toBeEnabled();
    expect(assignMock).not.toHaveBeenCalled();
  });

  it("sends an owner who already has a plan to the dashboard", async () => {
    startCheckoutMock.mockResolvedValue({ kind: "subscription_active" });
    render(<PlanChooser plan={PLAN} canceled={false} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Subscribe" }));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/dashboard"));
  });
});

describe("CheckoutReturn", () => {
  it("never claims success while Stripe hasn't confirmed, even with a session_id", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    syncCheckoutMock.mockResolvedValue({ kind: "ok", data: access({ status: "incomplete" }) });

    render(<CheckoutReturn sessionId="cs_test_123" />);

    expect(screen.getByText("Confirming your plan")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });

    expect(await screen.findByText("Still confirming")).toBeInTheDocument();
    expect(screen.queryByText("You're all set")).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalledWith("/dashboard");
    expect(syncCheckoutMock).toHaveBeenCalledWith("token-123", "cs_test_123");
    // One immediate sync plus one per backoff step, then it stops.
    expect(syncCheckoutMock).toHaveBeenCalledTimes(7);
  });

  it("goes to the dashboard once the API reports the plan as entitled", async () => {
    syncCheckoutMock.mockResolvedValue({
      kind: "ok",
      data: access({ entitled: true, status: "trialing" }),
    });

    render(<CheckoutReturn sessionId="cs_test_123" />);

    expect(await screen.findByText("You're all set")).toBeInTheDocument();
    expect(replaceMock).toHaveBeenCalledWith("/dashboard");
  });

  it("recovers when a later sync sees Stripe confirm, even after a transient failure", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    syncCheckoutMock
      .mockResolvedValueOnce({ kind: "unreachable", reason: "timeout" })
      .mockResolvedValueOnce({ kind: "ok", data: access() })
      .mockResolvedValue({ kind: "ok", data: access({ entitled: true, status: "active" }) });

    render(<CheckoutReturn sessionId="cs_test_123" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_500);
    });

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/dashboard"));
  });

  it("sends an expired session to sign-in instead of polling on", async () => {
    syncCheckoutMock.mockResolvedValue({ kind: "unauthorized" });

    render(<CheckoutReturn sessionId="cs_test_123" />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/sign-in"));
    expect(syncCheckoutMock).toHaveBeenCalledTimes(1);
  });

  it("stops polling once it unmounts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    syncCheckoutMock.mockResolvedValue({ kind: "ok", data: access() });

    const { unmount } = render(<CheckoutReturn sessionId="cs_test_123" />);
    await waitFor(() => expect(syncCheckoutMock).toHaveBeenCalledTimes(1));
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });

    expect(syncCheckoutMock).toHaveBeenCalledTimes(1);
  });

  it("reports an unknown checkout", async () => {
    syncCheckoutMock.mockResolvedValue({ kind: "not_found" });

    render(<CheckoutReturn sessionId="cs_test_someone_else" />);

    expect(await screen.findByText("We couldn't find that checkout")).toBeInTheDocument();
  });

  it("reports a missing session id without calling the API", async () => {
    render(<CheckoutReturn sessionId={null} />);

    expect(await screen.findByText("We couldn't find that checkout")).toBeInTheDocument();
    expect(syncCheckoutMock).not.toHaveBeenCalled();
  });
});

describe("BillingStatus", () => {
  const now = new Date("2026-10-20T12:00:00Z");

  it("counts down the trial", () => {
    render(
      <BillingStatus
        now={now}
        timeZone="America/New_York"
        subscription={access({
          entitled: true,
          status: "trialing",
          trial_ends_at: "2026-11-03T00:00:00Z",
        })}
      />,
    );
    expect(screen.getByText("Free trial: 14 days left")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage billing" })).toBeInTheDocument();
  });

  it("warns about a failed payment with a way to fix it", () => {
    render(<BillingStatus now={now} timeZone="America/New_York" subscription={access({ entitled: true, status: "past_due" })} />);
    expect(screen.getByText("Payment failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage billing" })).toBeInTheDocument();
  });

  it("shows when a canceled plan ends", () => {
    render(
      <BillingStatus
        now={now}
        timeZone="America/New_York"
        subscription={access({
          entitled: true,
          status: "active",
          cancel_at_period_end: true,
          current_period_end: "2026-11-20T00:00:00Z",
        })}
      />,
    );
    expect(screen.getByText("Active")).toBeInTheDocument();
    // 2026-11-20T00:00Z is still Nov 19 in New York.
    expect(screen.getByText("Ends on November 19, 2026")).toBeInTheDocument();
  });

  it("opens the Stripe portal from Manage billing", async () => {
    openBillingPortalMock.mockResolvedValue({ kind: "ok", url: "https://billing.stripe.com/p/1" });
    render(<BillingStatus now={now} timeZone="America/New_York" subscription={access({ entitled: true, status: "active" })} />);

    await userEvent.setup().click(screen.getByRole("button", { name: "Manage billing" }));

    await waitFor(() => expect(assignMock).toHaveBeenCalledWith("https://billing.stripe.com/p/1"));
  });
});
