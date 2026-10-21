import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const refreshMock = vi.fn();
const getTokenMock = vi.fn();
const updateAgentMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshMock, replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  updateAgent: (...args: unknown[]) => updateAgentMock(...args),
}));

import type { AgentRead } from "@/lib/api/client";
import { AgentBuilder } from "./agent-builder";

const AGENT: AgentRead = {
  id: "a1",
  name: "Riley",
  tone: "friendly",
  greeting: "Hi, thanks for contacting Acme!",
  custom_instructions: "",
  active: true,
  updated_at: "2026-01-01T00:00:00Z",
};

describe("AgentBuilder", () => {
  beforeEach(() => {
    refreshMock.mockReset();
    getTokenMock.mockReset().mockResolvedValue("token-123");
    updateAgentMock.mockReset();
  });

  it("disables save until something changes", async () => {
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} />);

    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText("Receptionist name"), "y");
    expect(save).toBeEnabled();
  });

  it("shows validation messages for a blank name and greeting and does not call the API", async () => {
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} />);

    await user.clear(screen.getByLabelText("Receptionist name"));
    await user.clear(screen.getByLabelText("Greeting"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Receptionist name is required.")).toBeInTheDocument();
    expect(screen.getByText("Greeting is required.")).toBeInTheDocument();
    expect(updateAgentMock).not.toHaveBeenCalled();
  });

  it("treats a whitespace-only name as blank", async () => {
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} />);

    await user.clear(screen.getByLabelText("Receptionist name"));
    await user.type(screen.getByLabelText("Receptionist name"), "   ");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Receptionist name is required.")).toBeInTheDocument();
    expect(updateAgentMock).not.toHaveBeenCalled();
  });

  it("rejects custom instructions over 2000 characters and shows a live counter", async () => {
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} />);

    const box = screen.getByLabelText(/custom instructions/i);
    await user.type(box, "abc");
    expect(screen.getByText("3/2000")).toBeInTheDocument();

    await user.clear(box);
    await user.click(box);
    await user.paste("a".repeat(2001));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Use 2000 characters or fewer.")).toBeInTheDocument();
    expect(updateAgentMock).not.toHaveBeenCalled();
  });

  it("describes each tone and explains the safety limit", () => {
    render(<AgentBuilder agent={AGENT} />);

    expect(screen.getByRole("radio", { name: /friendly/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /professional/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /concise/i })).toBeInTheDocument();
    expect(screen.getByText(/cannot override Tether/i)).toBeInTheDocument();
  });

  it("sends only the changed fields, then shows Saved and refreshes", async () => {
    updateAgentMock.mockResolvedValue({
      kind: "ok",
      data: { ...AGENT, name: "Sam", tone: "concise" },
    });
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} />);

    const name = screen.getByLabelText("Receptionist name");
    await user.clear(name);
    await user.type(name, "  Sam  ");
    await user.click(screen.getByRole("radio", { name: /concise/i }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateAgentMock).toHaveBeenCalledTimes(1));
    expect(updateAgentMock).toHaveBeenCalledWith("token-123", { name: "Sam", tone: "concise" });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(refreshMock).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("does not show Saved while the request is still in flight", async () => {
    let resolve: (value: unknown) => void = () => {};
    updateAgentMock.mockReturnValue(new Promise((r) => (resolve = r)));
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} />);

    await user.type(screen.getByLabelText("Receptionist name"), "y");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();

    resolve({ kind: "ok", data: { ...AGENT, name: "Rileyy" } });
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("shows API field errors inline on a 422", async () => {
    updateAgentMock.mockResolvedValue({
      kind: "validation",
      message: "Invalid.",
      fieldErrors: { name: "That name is not allowed." },
    });
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} />);

    await user.type(screen.getByLabelText("Receptionist name"), "y");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("That name is not allowed.")).toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("shows a banner for a 422 without field errors and for an unreachable API", async () => {
    updateAgentMock.mockResolvedValueOnce({ kind: "validation", message: "Bad input.", fieldErrors: {} });
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} />);

    await user.type(screen.getByLabelText("Receptionist name"), "y");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Bad input.")).toBeInTheDocument();

    updateAgentMock.mockResolvedValueOnce({ kind: "unreachable", reason: "Could not reach the API." });
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Could not reach the API.")).toBeInTheDocument();
    expect(refreshMock).not.toHaveBeenCalled();
  });
});
