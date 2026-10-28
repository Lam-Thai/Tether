import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const refreshMock = vi.fn();
const getTokenMock = vi.fn();
const updateAgentMock = vi.fn();
const previewVoiceMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshMock, replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({ getToken: getTokenMock }),
}));

vi.mock("@/lib/api/client", () => ({
  updateAgent: (...args: unknown[]) => updateAgentMock(...args),
  previewVoice: (...args: unknown[]) => previewVoiceMock(...args),
}));

import type { AgentRead, VoiceListResult } from "@/lib/api/client";
import { AgentBuilder } from "./agent-builder";

const AGENT: AgentRead = {
  id: "a1",
  name: "Riley",
  tone: "friendly",
  greeting: "Hi, thanks for contacting Acme!",
  custom_instructions: "",
  voice: "Kore",
  active: true,
  updated_at: "2026-01-01T00:00:00Z",
};

const VOICE_DATA = {
  voices: [
    { id: "Kore", label: "Kore", description: "Firm" },
    { id: "Puck", label: "Puck", description: "Upbeat" },
    { id: "standard", label: "Standard phone voice", description: "Twilio's built-in voice" },
  ],
  default_voice: "Kore",
  tts_configured: true,
};
const VOICES: VoiceListResult = { kind: "ok", data: VOICE_DATA };

describe("AgentBuilder", () => {
  beforeEach(() => {
    refreshMock.mockReset();
    getTokenMock.mockReset().mockResolvedValue("token-123");
    updateAgentMock.mockReset();
    previewVoiceMock.mockReset();
    URL.createObjectURL = vi.fn(() => "blob:preview-1");
    URL.revokeObjectURL = vi.fn();
  });

  it("disables save until something changes", async () => {
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText("Receptionist name"), "y");
    expect(save).toBeEnabled();
  });

  it("shows validation messages for a blank name and greeting and does not call the API", async () => {
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

    await user.clear(screen.getByLabelText("Receptionist name"));
    await user.clear(screen.getByLabelText("Greeting"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Receptionist name is required.")).toBeInTheDocument();
    expect(screen.getByText("Greeting is required.")).toBeInTheDocument();
    expect(updateAgentMock).not.toHaveBeenCalled();
  });

  it("treats a whitespace-only name as blank", async () => {
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

    await user.clear(screen.getByLabelText("Receptionist name"));
    await user.type(screen.getByLabelText("Receptionist name"), "   ");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Receptionist name is required.")).toBeInTheDocument();
    expect(updateAgentMock).not.toHaveBeenCalled();
  });

  it("rejects custom instructions over 2000 characters and shows a live counter", async () => {
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

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
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

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
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

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
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

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
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

    await user.type(screen.getByLabelText("Receptionist name"), "y");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("That name is not allowed.")).toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("shows a banner for a 422 without field errors and for an unreachable API", async () => {
    updateAgentMock.mockResolvedValueOnce({ kind: "validation", message: "Bad input.", fieldErrors: {} });
    const user = userEvent.setup();
    render(<AgentBuilder agent={AGENT} voices={VOICES} />);

    await user.type(screen.getByLabelText("Receptionist name"), "y");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Bad input.")).toBeInTheDocument();

    updateAgentMock.mockResolvedValueOnce({ kind: "unreachable", reason: "Could not reach the API." });
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Could not reach the API.")).toBeInTheDocument();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  describe("voice", () => {
    it("lists the server voices with the saved one selected", () => {
      render(<AgentBuilder agent={AGENT} voices={VOICES} />);

      expect(screen.getByLabelText("Voice")).toHaveValue("Kore");
      expect(screen.getByRole("option", { name: "Puck (Upbeat)" })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: "Standard phone voice" })).toBeInTheDocument();
    });

    it("marks the form dirty and PATCHes only the voice", async () => {
      updateAgentMock.mockResolvedValue({ kind: "ok", data: { ...AGENT, voice: "Puck" } });
      const user = userEvent.setup();
      render(<AgentBuilder agent={AGENT} voices={VOICES} />);

      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
      await user.selectOptions(screen.getByLabelText("Voice"), "Puck");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() => expect(updateAgentMock).toHaveBeenCalledTimes(1));
      expect(updateAgentMock).toHaveBeenCalledWith("token-123", { voice: "Puck" });
      expect(await screen.findByText("Saved")).toBeInTheDocument();
    });

    it("previews the selected, unsaved voice and renders an audio player", async () => {
      previewVoiceMock.mockResolvedValue({ kind: "ok", audio: new Blob(["x"], { type: "audio/wav" }) });
      const user = userEvent.setup();
      const { container } = render(<AgentBuilder agent={AGENT} voices={VOICES} />);

      await user.selectOptions(screen.getByLabelText("Voice"), "Puck");
      await user.click(screen.getByRole("button", { name: "Preview voice" }));

      await waitFor(() => expect(container.querySelector("audio")).not.toBeNull());
      expect(previewVoiceMock).toHaveBeenCalledWith("token-123", "Puck");
      expect(container.querySelector("audio")).toHaveAttribute("src", "blob:preview-1");
    });

    it("shows Generating... and disables the button while the preview is in flight", async () => {
      let resolve: (value: unknown) => void = () => {};
      previewVoiceMock.mockReturnValue(new Promise((r) => (resolve = r)));
      const user = userEvent.setup();
      const { container } = render(<AgentBuilder agent={AGENT} voices={VOICES} />);

      await user.click(screen.getByRole("button", { name: "Preview voice" }));
      expect(await screen.findByRole("button", { name: "Generating..." })).toBeDisabled();
      expect(container.querySelector("audio")).toBeNull();

      resolve({ kind: "usage_limited" });
      expect(await screen.findByRole("button", { name: "Preview voice" })).toBeEnabled();
    });

    it("revokes the previous object URL when a new preview replaces it", async () => {
      previewVoiceMock.mockResolvedValue({ kind: "ok", audio: new Blob(["x"], { type: "audio/wav" }) });
      const user = userEvent.setup();
      render(<AgentBuilder agent={AGENT} voices={VOICES} />);

      await user.click(screen.getByRole("button", { name: "Preview voice" }));
      await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
      await user.click(screen.getByRole("button", { name: "Preview voice" }));

      await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1"));
    });

    it.each([
      ["usage_limited", {}, "Today's voice preview limit is reached. It resets at 00:00 UTC."],
      [
        "unavailable",
        {},
        "Voice preview isn't available right now. Calls will use the standard voice until it is.",
      ],
      ["invalid", { message: "Unknown voice." }, "Unknown voice."],
      ["unreachable", { reason: "Could not reach the API." }, "Could not reach the API."],
    ])("shows copy for a %s preview result", async (kind, extra, copy) => {
      previewVoiceMock.mockResolvedValue({ kind, ...extra });
      const user = userEvent.setup();
      const { container } = render(<AgentBuilder agent={AGENT} voices={VOICES} />);

      await user.click(screen.getByRole("button", { name: "Preview voice" }));

      expect(await screen.findByText(copy)).toBeInTheDocument();
      expect(container.querySelector("audio")).toBeNull();
    });

    it("disables preview for the standard voice and explains why", async () => {
      const user = userEvent.setup();
      render(<AgentBuilder agent={AGENT} voices={VOICES} />);

      await user.selectOptions(screen.getByLabelText("Voice"), "standard");

      expect(screen.getByRole("button", { name: "Preview voice" })).toBeDisabled();
      expect(screen.getByText(/nothing to preview/i)).toBeInTheDocument();
    });

    it("says the preview uses the saved greeting when the greeting has unsaved edits", async () => {
      const user = userEvent.setup();
      render(<AgentBuilder agent={AGENT} voices={VOICES} />);

      expect(screen.queryByText(/unsaved edits/i)).not.toBeInTheDocument();
      await user.type(screen.getByLabelText("Greeting"), " Welcome.");
      expect(screen.getByText(/unsaved edits, so the preview uses the saved greeting/i)).toBeInTheDocument();
    });

    it("shows the demo-mode note and disables preview when TTS is not configured", () => {
      const demo: VoiceListResult = { kind: "ok", data: { ...VOICE_DATA, tts_configured: false } };
      render(<AgentBuilder agent={{ ...AGENT, voice: "standard" }} voices={demo} />);

      expect(screen.getByText("Demo mode")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Preview voice" })).toBeDisabled();
    });

    it("keeps the rest of the form usable when voices fail to load", async () => {
      updateAgentMock.mockResolvedValue({ kind: "ok", data: { ...AGENT, name: "Rileyy" } });
      const user = userEvent.setup();
      render(
        <AgentBuilder agent={AGENT} voices={{ kind: "unreachable", reason: "Could not reach the API." }} />,
      );

      expect(screen.getByText(/Voices could not be loaded/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Preview voice" })).not.toBeInTheDocument();

      await user.type(screen.getByLabelText("Receptionist name"), "y");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() => expect(updateAgentMock).toHaveBeenCalledWith("token-123", { name: "Rileyy" }));
    });
  });
});
