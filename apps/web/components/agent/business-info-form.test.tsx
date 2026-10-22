import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
const updateBusinessMock = vi.fn();
const refreshMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshMock, replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  updateBusiness: (...args: unknown[]) => updateBusinessMock(...args),
}));

import type { BusinessRead } from "@/lib/api/client";
import { BusinessInfoForm } from "./business-info-form";

const BUSINESS: BusinessRead = {
  id: "b1",
  name: "Acme Dental",
  industry: "Dental",
  address: "123 Main St",
  timezone: "America/New_York",
  phone: "555-0100",
  contact_email: "owner@acme.test",
  currency: "USD",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("BusinessInfoForm", () => {
  beforeEach(() => {
    getTokenMock.mockReset().mockResolvedValue("token-123");
    updateBusinessMock.mockReset();
    refreshMock.mockReset();
  });

  it("pre-fills the saved values and says the receptionist states them", () => {
    render(<BusinessInfoForm result={{ kind: "ok", data: BUSINESS }} />);

    expect(screen.getByLabelText("Business name")).toHaveValue("Acme Dental");
    expect(screen.getByLabelText("Timezone")).toHaveValue("America/New_York");
    expect(screen.getByLabelText("Currency")).toHaveValue("USD");
    expect(screen.getByText(/tells customers this/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("keeps a current timezone and currency that are not in the lists", () => {
    render(
      <BusinessInfoForm
        result={{ kind: "ok", data: { ...BUSINESS, timezone: "Etc/Odd-Zone", currency: "XTS" } }}
      />,
    );

    expect(screen.getByLabelText("Timezone")).toHaveValue("Etc/Odd-Zone");
    expect(screen.getByLabelText("Currency")).toHaveValue("XTS");
  });

  it("validates required fields and the email shape without calling the API", async () => {
    const user = userEvent.setup();
    render(<BusinessInfoForm result={{ kind: "ok", data: BUSINESS }} />);

    await user.clear(screen.getByLabelText("Business name"));
    await user.clear(screen.getByLabelText("Contact email (optional)"));
    await user.type(screen.getByLabelText("Contact email (optional)"), "nope");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Business name is required.")).toBeInTheDocument();
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(updateBusinessMock).not.toHaveBeenCalled();
  });

  it("PATCHes only the changed fields and clears blank phone with null", async () => {
    updateBusinessMock.mockResolvedValue({
      kind: "ok",
      data: { ...BUSINESS, address: "9 Elm St", phone: null, currency: "JPY" },
    });
    const user = userEvent.setup();
    render(<BusinessInfoForm result={{ kind: "ok", data: BUSINESS }} />);

    const address = screen.getByLabelText("Address");
    await user.clear(address);
    await user.type(address, "  9 Elm St  ");
    await user.clear(screen.getByLabelText("Phone (optional)"));
    await user.selectOptions(screen.getByLabelText("Currency"), "JPY");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateBusinessMock).toHaveBeenCalledTimes(1));
    expect(updateBusinessMock).toHaveBeenCalledWith("token-123", "b1", {
      address: "9 Elm St",
      phone: null,
      currency: "JPY",
    });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(refreshMock).toHaveBeenCalled();
  });

  it("shows Saved only after the API confirms and an error otherwise", async () => {
    let resolve: (value: unknown) => void = () => {};
    updateBusinessMock.mockReturnValueOnce(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    render(<BusinessInfoForm result={{ kind: "ok", data: BUSINESS }} />);

    await user.type(screen.getByLabelText("Industry"), "s");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(updateBusinessMock).toHaveBeenCalled());
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();

    resolve({ kind: "invalid", message: "Industry is not allowed." });
    expect(await screen.findByRole("alert")).toHaveTextContent("Industry is not allowed.");
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("shows unreachable failures from the save", async () => {
    updateBusinessMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
    const user = userEvent.setup();
    render(<BusinessInfoForm result={{ kind: "ok", data: BUSINESS }} />);

    await user.type(screen.getByLabelText("Industry"), "s");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the API.");

    // Correcting a field clears the stale error.
    await user.type(screen.getByLabelText("Industry"), "x");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a callout when the business could not load", () => {
    const { unmount } = render(
      <BusinessInfoForm result={{ kind: "unreachable", reason: "Could not reach the API." }} />,
    );
    expect(screen.getByText("Can't load your business information")).toBeInTheDocument();
    unmount();
    render(<BusinessInfoForm result={{ kind: "unauthorized" }} />);
    expect(screen.getByRole("link", { name: "Sign in again" })).toBeInTheDocument();
  });
});
