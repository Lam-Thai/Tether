import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
const createServiceMock = vi.fn();
const updateServiceMock = vi.fn();
const deleteServiceMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  createService: (...args: unknown[]) => createServiceMock(...args),
  updateService: (...args: unknown[]) => updateServiceMock(...args),
  deleteService: (...args: unknown[]) => deleteServiceMock(...args),
}));

import type { ServiceListResult, ServiceRead } from "@/lib/api/client";
import { ServicesManager } from "./services-manager";

const CLEANING: ServiceRead = {
  id: "s1",
  name: "Cleaning",
  description: "A full clean.",
  duration_minutes: 90,
  price: "45.00",
  active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const CHECKUP: ServiceRead = { ...CLEANING, id: "s2", name: "Checkup", description: null, duration_minutes: 45, price: null };

function ok(services: ServiceRead[], max = 50, currency = "USD"): ServiceListResult {
  return { kind: "ok", data: { services, currency, max_services: max } };
}

describe("ServicesManager", () => {
  beforeEach(() => {
    getTokenMock.mockReset().mockResolvedValue("token-123");
    createServiceMock.mockReset();
    updateServiceMock.mockReset();
    deleteServiceMock.mockReset();
  });

  it("explains services and offers Add when empty", () => {
    render(<ServicesManager result={ok([])} />);

    expect(screen.getByText("No services yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add your first service" })).toBeEnabled();
  });

  it("lists services with duration, price, switch state and the inactive note", () => {
    render(<ServicesManager result={ok([CLEANING, { ...CHECKUP, active: false }])} />);

    expect(screen.getByText("1 h 30 min")).toBeInTheDocument();
    expect(screen.getByText("$45.00")).toBeInTheDocument();
    expect(screen.getByText("45 min")).toBeInTheDocument();
    expect(screen.getByText("No listed price")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Active: Cleaning" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Active: Checkup" })).not.toBeChecked();
    expect(screen.getByText(/never offered or priced/i)).toBeInTheDocument();
  });

  it("formats JPY with no decimals", () => {
    render(<ServicesManager result={ok([{ ...CLEANING, price: "5000.00" }], 50, "JPY")} />);
    expect(screen.getByText("¥5,000")).toBeInTheDocument();
  });

  it("disables Add at the limit and says why", () => {
    render(<ServicesManager result={ok([CLEANING], 1)} />);

    expect(screen.getByRole("button", { name: "Add service" })).toBeDisabled();
    expect(screen.getByText(/reached the limit of 1 services/i)).toBeInTheDocument();
  });

  it("shows error, plan-required and signed-out callouts", () => {
    const { unmount } = render(
      <ServicesManager result={{ kind: "unreachable", reason: "Could not reach the API." }} />,
    );
    expect(screen.getByText("Can't load your services")).toBeInTheDocument();
    unmount();
    const second = render(<ServicesManager result={{ kind: "subscription_required" }} />);
    expect(screen.getByRole("link", { name: "Choose plan" })).toHaveAttribute("href", "/billing/plan");
    second.unmount();
    render(<ServicesManager result={{ kind: "unauthorized" }} />);
    expect(screen.getByRole("link", { name: "Sign in again" })).toBeInTheDocument();
  });

  describe("add dialog", () => {
    it("validates required fields, price decimals and duration without calling the API", async () => {
      const user = userEvent.setup();
      render(<ServicesManager result={ok([])} />);

      await user.click(screen.getByRole("button", { name: "Add your first service" }));
      const dialog = await screen.findByRole("dialog", { name: "Add service" });
      await user.click(within(dialog).getByRole("button", { name: "Add service" }));

      expect(await within(dialog).findByText("Name is required.")).toBeInTheDocument();
      expect(within(dialog).getByText("Duration is required.")).toBeInTheDocument();

      await user.type(within(dialog).getByLabelText("Name"), "Whitening");
      await user.type(within(dialog).getByLabelText("Duration (minutes)"), "30");
      await user.type(within(dialog).getByLabelText(/Price in USD/), "10.999");
      await user.click(within(dialog).getByRole("button", { name: "Add service" }));

      expect(await within(dialog).findByText(/Enter an amount like 45 or 45.50 in USD/)).toBeInTheDocument();
      expect(createServiceMock).not.toHaveBeenCalled();
    });

    it("rejects decimals for a zero-decimal currency", async () => {
      const user = userEvent.setup();
      render(<ServicesManager result={ok([], 50, "JPY")} />);

      await user.click(screen.getByRole("button", { name: "Add your first service" }));
      const dialog = await screen.findByRole("dialog", { name: "Add service" });
      await user.type(within(dialog).getByLabelText("Name"), "Cut");
      await user.type(within(dialog).getByLabelText("Duration (minutes)"), "30");
      await user.type(within(dialog).getByLabelText(/Price in JPY/), "100.5");
      await user.click(within(dialog).getByRole("button", { name: "Add service" }));

      expect(await within(dialog).findByText(/whole amount in JPY/)).toBeInTheDocument();
      expect(createServiceMock).not.toHaveBeenCalled();
    });

    it("creates with the converted body and lists it only after the API confirms", async () => {
      let resolve: (value: unknown) => void = () => {};
      createServiceMock.mockReturnValue(new Promise((r) => (resolve = r)));
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING])} />);

      await user.click(screen.getByRole("button", { name: "Add service" }));
      const dialog = await screen.findByRole("dialog", { name: "Add service" });
      await user.type(within(dialog).getByLabelText("Name"), "  Whitening  ");
      await user.type(within(dialog).getByLabelText("Duration (minutes)"), "30");
      await user.type(within(dialog).getByLabelText(/Price in USD/), "99");
      await user.click(within(dialog).getByRole("button", { name: "Add service" }));

      await waitFor(() => expect(createServiceMock).toHaveBeenCalledTimes(1));
      expect(createServiceMock).toHaveBeenCalledWith("token-123", {
        name: "Whitening",
        description: null,
        duration_minutes: 30,
        price: "99",
        active: true,
      });
      expect(screen.queryByText("Whitening", { selector: "p" })).not.toBeInTheDocument();

      resolve({ kind: "ok", data: { ...CHECKUP, id: "s3", name: "Whitening", duration_minutes: 30, price: "99.00" } });
      expect(await screen.findByText("Whitening", { selector: "p" })).toBeInTheDocument();
    });

    it("sends a null price when left blank", async () => {
      createServiceMock.mockResolvedValue({ kind: "ok", data: CHECKUP });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([])} />);

      await user.click(screen.getByRole("button", { name: "Add your first service" }));
      const dialog = await screen.findByRole("dialog", { name: "Add service" });
      await user.type(within(dialog).getByLabelText("Name"), "Checkup");
      await user.type(within(dialog).getByLabelText("Duration (minutes)"), "45");
      await user.click(within(dialog).getByRole("button", { name: "Add service" }));

      await waitFor(() => expect(createServiceMock).toHaveBeenCalled());
      expect(createServiceMock.mock.calls[0]![1]).toMatchObject({ price: null });
    });

    it("shows a 409 name-taken error on the name field", async () => {
      createServiceMock.mockResolvedValue({ kind: "name_taken" });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING])} />);

      await user.click(screen.getByRole("button", { name: "Add service" }));
      const dialog = await screen.findByRole("dialog", { name: "Add service" });
      const name = within(dialog).getByLabelText("Name");
      await user.type(name, "cleaning");
      await user.type(within(dialog).getByLabelText("Duration (minutes)"), "30");
      await user.click(within(dialog).getByRole("button", { name: "Add service" }));

      expect(await within(dialog).findByText("You already have a service with this name.")).toBeInTheDocument();
      expect(name).toHaveAttribute("aria-invalid", "true");
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    });

    it("handles a 409 limit response in the dialog", async () => {
      createServiceMock.mockResolvedValue({ kind: "limit_reached" });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([])} />);

      await user.click(screen.getByRole("button", { name: "Add your first service" }));
      const dialog = await screen.findByRole("dialog", { name: "Add service" });
      await user.type(within(dialog).getByLabelText("Name"), "X");
      await user.type(within(dialog).getByLabelText("Duration (minutes)"), "30");
      await user.click(within(dialog).getByRole("button", { name: "Add service" }));

      expect(await within(dialog).findByText(/reached the service limit/i)).toBeInTheDocument();
    });

    it("shows plan and session errors in the dialog", async () => {
      createServiceMock.mockResolvedValue({ kind: "subscription_required" });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([])} />);

      await user.click(screen.getByRole("button", { name: "Add your first service" }));
      const dialog = await screen.findByRole("dialog", { name: "Add service" });
      await user.type(within(dialog).getByLabelText("Name"), "X");
      await user.type(within(dialog).getByLabelText("Duration (minutes)"), "30");
      await user.click(within(dialog).getByRole("button", { name: "Add service" }));

      expect(await within(dialog).findByText(/plan is not active/i)).toBeInTheDocument();
    });
  });

  describe("edit dialog", () => {
    it("sends only the changed fields and clears the price with null", async () => {
      updateServiceMock.mockResolvedValue({ kind: "ok", data: { ...CLEANING, duration_minutes: 60, price: null } });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING])} />);

      await user.click(screen.getByRole("button", { name: "Edit: Cleaning" }));
      const dialog = await screen.findByRole("dialog", { name: "Edit service" });
      expect(within(dialog).getByLabelText(/Price in USD/)).toHaveValue("45.00");
      const duration = within(dialog).getByLabelText("Duration (minutes)");
      await user.clear(duration);
      await user.type(duration, "60");
      await user.clear(within(dialog).getByLabelText(/Price in USD/));
      await user.click(within(dialog).getByRole("button", { name: "Save service" }));

      await waitFor(() => expect(updateServiceMock).toHaveBeenCalledTimes(1));
      expect(updateServiceMock).toHaveBeenCalledWith("token-123", "s1", { duration_minutes: 60, price: null });
      expect(await screen.findByText("1 h")).toBeInTheDocument();
    });

    it("does not treat 45 as a change from 45.00", async () => {
      updateServiceMock.mockResolvedValue({ kind: "ok", data: { ...CLEANING, name: "Deep clean" } });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING])} />);

      await user.click(screen.getByRole("button", { name: "Edit: Cleaning" }));
      const dialog = await screen.findByRole("dialog", { name: "Edit service" });
      const price = within(dialog).getByLabelText(/Price in USD/);
      await user.clear(price);
      await user.type(price, "45");
      const name = within(dialog).getByLabelText("Name");
      await user.clear(name);
      await user.type(name, "Deep clean");
      await user.click(within(dialog).getByRole("button", { name: "Save service" }));

      await waitFor(() => expect(updateServiceMock).toHaveBeenCalled());
      expect(updateServiceMock).toHaveBeenCalledWith("token-123", "s1", { name: "Deep clean" });
    });

    it("keeps Save disabled until something changes", async () => {
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING])} />);

      await user.click(screen.getByRole("button", { name: "Edit: Cleaning" }));
      const dialog = await screen.findByRole("dialog", { name: "Edit service" });
      expect(within(dialog).getByRole("button", { name: "Save service" })).toBeDisabled();
    });
  });

  describe("active switch", () => {
    it("waits for the API before moving", async () => {
      let resolve: (value: unknown) => void = () => {};
      updateServiceMock.mockReturnValue(new Promise((r) => (resolve = r)));
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING])} />);

      const toggle = screen.getByRole("switch", { name: "Active: Cleaning" });
      await user.click(toggle);

      await waitFor(() => expect(updateServiceMock).toHaveBeenCalledWith("token-123", "s1", { active: false }));
      expect(toggle).toBeDisabled();
      expect(toggle).toBeChecked();

      resolve({ kind: "ok", data: { ...CLEANING, active: false } });
      await waitFor(() => expect(toggle).not.toBeChecked());
      expect(toggle).toBeEnabled();
    });

    it("stays put and shows an error when the request fails", async () => {
      updateServiceMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING])} />);

      const toggle = screen.getByRole("switch", { name: "Active: Cleaning" });
      await user.click(toggle);

      expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the API.");
      expect(toggle).toBeChecked();
    });

    it("drops a service the API says no longer exists", async () => {
      updateServiceMock.mockResolvedValue({ kind: "not_found" });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING, CHECKUP])} />);

      await user.click(screen.getByRole("switch", { name: "Active: Cleaning" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(/no longer exists/i);
      expect(screen.queryByRole("switch", { name: "Active: Cleaning" })).not.toBeInTheDocument();
      expect(screen.getByRole("switch", { name: "Active: Checkup" })).toBeInTheDocument();
    });
  });

  describe("delete", () => {
    it("requires confirmation and removes only after the API confirms", async () => {
      deleteServiceMock.mockResolvedValue({ kind: "ok" });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING, CHECKUP])} />);

      await user.click(screen.getByRole("button", { name: "Delete: Cleaning" }));
      const dialog = await screen.findByRole("dialog", { name: "Delete service?" });
      expect(deleteServiceMock).not.toHaveBeenCalled();

      await user.click(within(dialog).getByRole("button", { name: "Delete service" }));

      await waitFor(() => expect(deleteServiceMock).toHaveBeenCalledWith("token-123", "s1"));
      await waitFor(() => expect(screen.queryByText("Cleaning", { selector: "p" })).not.toBeInTheDocument());
      expect(screen.getByText("Checkup", { selector: "p" })).toBeInTheDocument();
    });

    it("a failure keeps the service and shows the error", async () => {
      deleteServiceMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
      const user = userEvent.setup();
      render(<ServicesManager result={ok([CLEANING])} />);

      await user.click(screen.getByRole("button", { name: "Delete: Cleaning" }));
      const dialog = await screen.findByRole("dialog", { name: "Delete service?" });
      await user.click(within(dialog).getByRole("button", { name: "Delete service" }));

      expect(await within(dialog).findByText("Could not reach the API.")).toBeInTheDocument();
      expect(screen.getByText("Cleaning", { selector: "p" })).toBeInTheDocument();
    });
  });
});
