import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const getTokenMock = vi.fn();
const createFaqMock = vi.fn();
const updateFaqMock = vi.fn();
const deleteFaqMock = vi.fn();

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  createFaq: (...args: unknown[]) => createFaqMock(...args),
  updateFaq: (...args: unknown[]) => updateFaqMock(...args),
  deleteFaq: (...args: unknown[]) => deleteFaqMock(...args),
}));

import type { FaqListResult, FaqRead } from "@/lib/api/client";
import { FaqManager } from "./faq-manager";

const HOURS: FaqRead = {
  id: "f1",
  question: "When are you open?",
  answer: "Monday to Friday, 9 to 5.",
  category: "Hours",
  active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const PARKING: FaqRead = { ...HOURS, id: "f2", question: "Is there parking?", answer: "Yes, free.", category: null };

function ok(faqs: FaqRead[], max = 50): FaqListResult {
  return { kind: "ok", data: { faqs, max_faqs: max } };
}

describe("FaqManager", () => {
  beforeEach(() => {
    getTokenMock.mockReset().mockResolvedValue("token-123");
    createFaqMock.mockReset();
    updateFaqMock.mockReset();
    deleteFaqMock.mockReset();
  });

  it("explains FAQs and offers Add when empty", () => {
    render(<FaqManager result={ok([])} />);

    expect(screen.getByText("No FAQs yet")).toBeInTheDocument();
    expect(screen.getByText(/the answers you approve/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add your first FAQ" })).toBeEnabled();
  });

  it("lists FAQs with category, switch state and the apply-next-reply note", () => {
    render(<FaqManager result={ok([HOURS, { ...PARKING, active: false }])} />);

    expect(screen.getByText("When are you open?")).toBeInTheDocument();
    expect(screen.getByText("Hours")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Active: When are you open?" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Active: Is there parking?" })).not.toBeChecked();
    expect(screen.getByText(/apply to its next reply/i)).toBeInTheDocument();
    expect(screen.getByText(/never used/i)).toBeInTheDocument();
  });

  it("disables Add at the limit and says why", () => {
    render(<FaqManager result={ok([HOURS], 1)} />);

    expect(screen.getByRole("button", { name: "Add FAQ" })).toBeDisabled();
    expect(screen.getByText(/reached the limit of 1 FAQs/i)).toBeInTheDocument();
  });

  it("shows an error callout when the list could not load", () => {
    render(<FaqManager result={{ kind: "unreachable", reason: "Could not reach the API." }} />);

    expect(screen.getByText("Can't load your FAQs")).toBeInTheDocument();
    expect(screen.getByText(/Could not reach the API\./)).toBeInTheDocument();
  });

  it("shows plan-required and signed-out callouts", () => {
    const { unmount } = render(<FaqManager result={{ kind: "subscription_required" }} />);
    expect(screen.getByRole("link", { name: "Choose plan" })).toHaveAttribute("href", "/billing/plan");
    unmount();
    render(<FaqManager result={{ kind: "unauthorized" }} />);
    expect(screen.getByRole("link", { name: "Sign in again" })).toBeInTheDocument();
  });

  describe("add dialog", () => {
    it("validates required fields and does not call the API", async () => {
      const user = userEvent.setup();
      render(<FaqManager result={ok([])} />);

      await user.click(screen.getByRole("button", { name: "Add your first FAQ" }));
      const dialog = await screen.findByRole("dialog", { name: "Add FAQ" });
      await user.click(within(dialog).getByRole("button", { name: "Add FAQ" }));

      expect(await within(dialog).findByText("Question is required.")).toBeInTheDocument();
      expect(within(dialog).getByText("Answer is required.")).toBeInTheDocument();
      expect(createFaqMock).not.toHaveBeenCalled();
    });

    it("rejects an answer over 1000 characters and shows a counter", async () => {
      const user = userEvent.setup();
      render(<FaqManager result={ok([])} />);

      await user.click(screen.getByRole("button", { name: "Add your first FAQ" }));
      const dialog = await screen.findByRole("dialog", { name: "Add FAQ" });
      await user.type(within(dialog).getByLabelText("Question"), "Q?");
      const answer = within(dialog).getByLabelText("Answer");
      await user.type(answer, "abc");
      expect(within(dialog).getByText("3/1000")).toBeInTheDocument();
      await user.clear(answer);
      await user.click(answer);
      await user.paste("a".repeat(1001));
      await user.click(within(dialog).getByRole("button", { name: "Add FAQ" }));

      expect(await within(dialog).findByText("Use 1000 characters or fewer.")).toBeInTheDocument();
      expect(createFaqMock).not.toHaveBeenCalled();
    });

    it("creates with the trimmed body and lists the FAQ only after the API confirms", async () => {
      let resolve: (value: unknown) => void = () => {};
      createFaqMock.mockReturnValue(new Promise((r) => (resolve = r)));
      const user = userEvent.setup();
      render(<FaqManager result={ok([HOURS])} />);

      await user.click(screen.getByRole("button", { name: "Add FAQ" }));
      const dialog = await screen.findByRole("dialog", { name: "Add FAQ" });
      await user.type(within(dialog).getByLabelText("Question"), "  Do you take cards?  ");
      await user.type(within(dialog).getByLabelText("Answer"), "Yes.");
      await user.click(within(dialog).getByRole("button", { name: "Add FAQ" }));

      await waitFor(() => expect(createFaqMock).toHaveBeenCalledTimes(1));
      expect(createFaqMock).toHaveBeenCalledWith("token-123", {
        question: "Do you take cards?",
        answer: "Yes.",
        category: null,
        active: true,
      });
      expect(screen.queryByText("Do you take cards?", { selector: "p" })).not.toBeInTheDocument();

      resolve({ kind: "ok", data: { ...PARKING, id: "f3", question: "Do you take cards?", answer: "Yes." } });
      expect(await screen.findByText("Do you take cards?", { selector: "p" })).toBeInTheDocument();
    });

    it("handles a 409 limit response in the dialog", async () => {
      createFaqMock.mockResolvedValue({ kind: "limit_reached" });
      const user = userEvent.setup();
      render(<FaqManager result={ok([])} />);

      await user.click(screen.getByRole("button", { name: "Add your first FAQ" }));
      const dialog = await screen.findByRole("dialog", { name: "Add FAQ" });
      await user.type(within(dialog).getByLabelText("Question"), "Q?");
      await user.type(within(dialog).getByLabelText("Answer"), "A.");
      await user.click(within(dialog).getByRole("button", { name: "Add FAQ" }));

      expect(await within(dialog).findByText(/reached the FAQ limit/i)).toBeInTheDocument();
    });

    it("offers existing categories in a datalist", async () => {
      const user = userEvent.setup();
      render(<FaqManager result={ok([HOURS, PARKING])} />);

      await user.click(screen.getByRole("button", { name: "Add FAQ" }));
      const dialog = await screen.findByRole("dialog", { name: "Add FAQ" });
      const input = within(dialog).getByLabelText("Category (optional)");
      const list = document.getElementById(input.getAttribute("list") ?? "");
      expect(list?.querySelectorAll("option")).toHaveLength(1);
      expect(list?.querySelector("option")).toHaveAttribute("value", "Hours");
    });
  });

  describe("edit dialog", () => {
    it("sends only the changed fields and clears a category with null", async () => {
      updateFaqMock.mockResolvedValue({ kind: "ok", data: { ...HOURS, answer: "Every day.", category: null } });
      const user = userEvent.setup();
      render(<FaqManager result={ok([HOURS])} />);

      await user.click(screen.getByRole("button", { name: "Edit: When are you open?" }));
      const dialog = await screen.findByRole("dialog", { name: "Edit FAQ" });
      const answer = within(dialog).getByLabelText("Answer");
      await user.clear(answer);
      await user.type(answer, "  Every day.  ");
      await user.clear(within(dialog).getByLabelText("Category (optional)"));
      await user.click(within(dialog).getByRole("button", { name: "Save FAQ" }));

      await waitFor(() => expect(updateFaqMock).toHaveBeenCalledTimes(1));
      expect(updateFaqMock).toHaveBeenCalledWith("token-123", "f1", { answer: "Every day.", category: null });
      expect(await screen.findByText("Every day.")).toBeInTheDocument();
    });

    it("keeps Save disabled until something changes", async () => {
      const user = userEvent.setup();
      render(<FaqManager result={ok([HOURS])} />);

      await user.click(screen.getByRole("button", { name: "Edit: When are you open?" }));
      const dialog = await screen.findByRole("dialog", { name: "Edit FAQ" });
      expect(within(dialog).getByRole("button", { name: "Save FAQ" })).toBeDisabled();
    });
  });

  describe("active switch", () => {
    it("waits for the API before moving", async () => {
      let resolve: (value: unknown) => void = () => {};
      updateFaqMock.mockReturnValue(new Promise((r) => (resolve = r)));
      const user = userEvent.setup();
      render(<FaqManager result={ok([HOURS])} />);

      const toggle = screen.getByRole("switch", { name: "Active: When are you open?" });
      await user.click(toggle);

      await waitFor(() =>
        expect(updateFaqMock).toHaveBeenCalledWith("token-123", "f1", { active: false }),
      );
      expect(toggle).toBeDisabled();
      expect(toggle).toBeChecked();

      resolve({ kind: "ok", data: { ...HOURS, active: false } });
      await waitFor(() => expect(toggle).not.toBeChecked());
      expect(toggle).toBeEnabled();
    });

    it("stays put and shows an error when the request fails", async () => {
      updateFaqMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
      const user = userEvent.setup();
      render(<FaqManager result={ok([HOURS])} />);

      const toggle = screen.getByRole("switch", { name: "Active: When are you open?" });
      await user.click(toggle);

      expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the API.");
      expect(toggle).toBeChecked();
      expect(toggle).toBeEnabled();
    });
  });

  describe("delete", () => {
    it("requires confirmation and removes only after the API confirms", async () => {
      deleteFaqMock.mockResolvedValue({ kind: "ok" });
      const user = userEvent.setup();
      render(<FaqManager result={ok([HOURS, PARKING])} />);

      await user.click(screen.getByRole("button", { name: "Delete: When are you open?" }));
      const dialog = await screen.findByRole("dialog", { name: "Delete FAQ?" });
      expect(deleteFaqMock).not.toHaveBeenCalled();

      await user.click(within(dialog).getByRole("button", { name: "Delete FAQ" }));

      await waitFor(() => expect(deleteFaqMock).toHaveBeenCalledWith("token-123", "f1"));
      await waitFor(() =>
        expect(screen.queryByText("When are you open?", { selector: "p" })).not.toBeInTheDocument(),
      );
      expect(screen.getByText("Is there parking?")).toBeInTheDocument();
    });

    it("cancelling does nothing and a failure keeps the FAQ", async () => {
      deleteFaqMock.mockResolvedValue({ kind: "unreachable", reason: "Could not reach the API." });
      const user = userEvent.setup();
      render(<FaqManager result={ok([HOURS])} />);

      await user.click(screen.getByRole("button", { name: "Delete: When are you open?" }));
      let dialog = await screen.findByRole("dialog", { name: "Delete FAQ?" });
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      expect(deleteFaqMock).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "Delete: When are you open?" }));
      dialog = await screen.findByRole("dialog", { name: "Delete FAQ?" });
      await user.click(within(dialog).getByRole("button", { name: "Delete FAQ" }));

      expect(await within(dialog).findByText("Could not reach the API.")).toBeInTheDocument();
      expect(screen.getByText("When are you open?", { selector: "p" })).toBeInTheDocument();
    });
  });
});
