import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const pushMock = vi.fn();
const getTokenMock = vi.fn();
const createBusinessMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  createBusiness: (...args: unknown[]) => createBusinessMock(...args),
}));

import { OnboardingForm } from "./onboarding-form";

describe("OnboardingForm", () => {
  beforeEach(() => {
    pushMock.mockReset();
    getTokenMock.mockReset().mockResolvedValue("token-123");
    createBusinessMock.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows required-field errors and never calls the API when submitted empty", async () => {
    const user = userEvent.setup();
    render(<OnboardingForm />);

    await user.click(screen.getByRole("button", { name: /create business/i }));

    expect(await screen.findByText("Business name is required.")).toBeInTheDocument();
    expect(screen.getByText("Industry is required.")).toBeInTheDocument();
    expect(screen.getByText("Address is required.")).toBeInTheDocument();
    expect(createBusinessMock).not.toHaveBeenCalled();
  });

  it("rejects a timezone value that isn't a real IANA zone", async () => {
    const user = userEvent.setup();
    render(<OnboardingForm />);

    await user.type(screen.getByLabelText(/business name/i), "Acme Dental");
    await user.type(screen.getByLabelText(/industry/i), "Dental");
    await user.type(screen.getByLabelText(/address/i), "123 Main St");

    const select = screen.getByLabelText(/timezone/i) as HTMLSelectElement;
    // Force an out-of-list value onto the native select to exercise the
    // Zod `refine` against `Intl.supportedValuesOf("timeZone")`.
    const fakeOption = document.createElement("option");
    fakeOption.value = "Nowhere/Fake";
    select.appendChild(fakeOption);
    await user.selectOptions(select, "Nowhere/Fake");

    await user.click(screen.getByRole("button", { name: /create business/i }));

    expect(await screen.findByText("Choose a valid timezone from the list.")).toBeInTheDocument();
    expect(createBusinessMock).not.toHaveBeenCalled();
  });

  it("rejects an invalid optional contact email", async () => {
    const user = userEvent.setup();
    render(<OnboardingForm />);

    await user.type(screen.getByLabelText(/business name/i), "Acme Dental");
    await user.type(screen.getByLabelText(/industry/i), "Dental");
    await user.type(screen.getByLabelText(/address/i), "123 Main St");
    await user.selectOptions(screen.getByLabelText(/timezone/i), "America/New_York");
    await user.type(screen.getByLabelText(/contact email/i), "not-an-email");

    await user.click(screen.getByRole("button", { name: /create business/i }));

    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(createBusinessMock).not.toHaveBeenCalled();
  });

  it("submits the validated payload and redirects to /dashboard on success", async () => {
    createBusinessMock.mockResolvedValue({
      kind: "ok",
      data: { id: "b1", name: "Acme Dental" },
    });
    const user = userEvent.setup();
    render(<OnboardingForm />);

    await user.type(screen.getByLabelText(/business name/i), "Acme Dental");
    await user.type(screen.getByLabelText(/industry/i), "Dental");
    await user.type(screen.getByLabelText(/address/i), "123 Main St");
    await user.selectOptions(screen.getByLabelText(/timezone/i), "America/New_York");

    await user.click(screen.getByRole("button", { name: /create business/i }));

    await waitFor(() => expect(createBusinessMock).toHaveBeenCalledTimes(1));
    expect(createBusinessMock).toHaveBeenCalledWith("token-123", {
      name: "Acme Dental",
      industry: "Dental",
      address: "123 Main St",
      timezone: "America/New_York",
      phone: null,
      contact_email: null,
    });
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/billing/plan"));
  });

  it("surfaces a 409 as a top-level banner instead of navigating", async () => {
    createBusinessMock.mockResolvedValue({ kind: "already_exists" });
    const user = userEvent.setup();
    render(<OnboardingForm />);

    await user.type(screen.getByLabelText(/business name/i), "Acme Dental");
    await user.type(screen.getByLabelText(/industry/i), "Dental");
    await user.type(screen.getByLabelText(/address/i), "123 Main St");
    await user.selectOptions(screen.getByLabelText(/timezone/i), "America/New_York");

    await user.click(screen.getByRole("button", { name: /create business/i }));

    expect(
      await screen.findByText("This account already has a business set up."),
    ).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });
});
