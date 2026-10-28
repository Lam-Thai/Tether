import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { listVoices, previewVoice, type VoiceList } from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

const VOICES: VoiceList = {
  voices: [
    { id: "Kore", label: "Kore", description: "Firm" },
    { id: "standard", label: "Standard phone voice", description: "Twilio's built-in voice" },
  ],
  default_voice: "Kore",
  tts_configured: true,
};

const error = (code: string, message = code) => ({ error: { code, message } });

function respondJson(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("voice client", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
  });

  describe("listVoices", () => {
    it("returns the voice list", async () => {
      const fetchMock = respondJson(200, VOICES);
      expect(await listVoices("tok")).toEqual({ kind: "ok", data: VOICES });
      expect(fetchMock.mock.calls[0]?.[0]).toBe("https://api.example.test/api/v1/agent/voices");
    });

    it("maps 401, 402, a malformed body and a thrown fetch", async () => {
      respondJson(401, error("unauthorized"));
      expect(await listVoices("tok")).toEqual({ kind: "unauthorized" });
      respondJson(402, error("subscription_required"));
      expect(await listVoices("tok")).toEqual({ kind: "subscription_required" });
      respondJson(200, { voices: "nope" });
      expect((await listVoices("tok")).kind).toBe("unreachable");
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await listVoices("tok")).kind).toBe("unreachable");
    });
  });

  describe("previewVoice", () => {
    it("returns the audio blob and posts the voice", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { "Content-Type": "audio/wav" } }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const result = await previewVoice("tok", "Kore");

      expect(result.kind).toBe("ok");
      if (result.kind === "ok") expect(result.audio.size).toBe(3);
      const [url, init] = fetchMock.mock.calls[0] ?? [];
      expect(url).toBe("https://api.example.test/api/v1/agent/voice-preview");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({ voice: "Kore" });
      expect(init.headers.Authorization).toBe("Bearer tok");
    });

    it("rejects a 200 that is not audio", async () => {
      respondJson(200, { hello: "world" });
      expect((await previewVoice("tok", "Kore")).kind).toBe("unreachable");
    });

    it.each([
      [429, "usage_limit_reached", "usage_limited"],
      [503, "tts_unavailable", "unavailable"],
      [503, "tts_not_configured", "unavailable"],
      [422, "unknown_voice", "invalid"],
      [422, "preview_unavailable", "invalid"],
      [401, "unauthorized", "unauthorized"],
      [402, "subscription_required", "subscription_required"],
    ])("maps %s %s to %s", async (status, code, kind) => {
      respondJson(status, error(code, "Nope."));
      const result = await previewVoice("tok", "Kore");
      expect(result.kind).toBe(kind);
      if (result.kind === "invalid") expect(result.message).toBe("Nope.");
    });

    it("treats an unknown 429 or a non-JSON error as unreachable", async () => {
      respondJson(429, error("concurrency_limited"));
      expect((await previewVoice("tok", "Kore")).kind).toBe("unreachable");
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("oops", { status: 500 })));
      expect((await previewVoice("tok", "Kore")).kind).toBe("unreachable");
    });

    it("reports a thrown fetch and a missing base URL as unreachable", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await previewVoice("tok", "Kore")).kind).toBe("unreachable");
      delete process.env.NEXT_PUBLIC_API_BASE_URL;
      expect((await previewVoice("tok", "Kore")).kind).toBe("unreachable");
    });
  });
});
