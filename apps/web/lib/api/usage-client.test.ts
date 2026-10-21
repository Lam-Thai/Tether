import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getUsage, sendTestMessage, type UsageRead } from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

const USAGE: UsageRead = {
  window: { timezone: "UTC", starts_at: "2026-10-21T00:00:00Z", resets_at: "2026-10-22T00:00:00Z" },
  meters: [
    { kind: "gemini_request", used: 3, limit: 50, global_limit_reached: false },
    { kind: "tts_request", used: 0, limit: 10, global_limit_reached: false },
    { kind: "call", used: 0, limit: 10, global_limit_reached: false },
    { kind: "sms", used: 0, limit: 10, global_limit_reached: false },
  ],
  gemini_tokens: 1834,
  failed_requests: 1,
  estimated_cost_usd: "0.004210",
  concurrency: { active: 1, limit: 3, idle_minutes: 15, global_limit_reached: false },
  max_call_duration_seconds: 600,
};

function respond(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const error = (code: string, message = code) => ({ error: { code, message } });

describe("getUsage", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  it("returns usage with a bearer token", async () => {
    const fetchMock = respond(200, USAGE);
    expect(await getUsage("tok")).toEqual({ kind: "ok", data: USAGE });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/usage");
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("accepts a meter kind it does not know", async () => {
    const data = {
      ...USAGE,
      meters: [...USAGE.meters, { kind: "whatsapp_message", used: 1, limit: 5, global_limit_reached: false }],
    };
    respond(200, data);
    expect(await getUsage("tok")).toEqual({ kind: "ok", data });
  });

  it("maps 401 and 402", async () => {
    respond(401, error("unauthorized"));
    expect(await getUsage("tok")).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await getUsage("tok")).toEqual({ kind: "subscription_required" });
  });

  it("is unreachable on an error body, a 5xx, a wrong shape, a thrown fetch, or unreadable JSON", async () => {
    respond(500, error("internal_error", "Boom."));
    expect((await getUsage("tok")).kind).toBe("unreachable");

    respond(200, { ...USAGE, meters: [{ kind: "call", used: "3", limit: 10, global_limit_reached: false }] });
    expect((await getUsage("tok")).kind).toBe("unreachable");

    respond(200, { ...USAGE, estimated_cost_usd: "free" });
    expect((await getUsage("tok")).kind).toBe("unreachable");

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    expect((await getUsage("tok")).kind).toBe("unreachable");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })));
    expect((await getUsage("tok")).kind).toBe("unreachable");
  });
});

describe("sendTestMessage quota fields", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  const turn = {
    interaction_id: "22222222-2222-4222-8222-222222222222",
    reply_text: "Sorry.",
    intent: "UNKNOWN",
    status: "active",
    ended: false,
    fallback: true,
    messages: [],
  };

  it("passes usage_limited through, and reads a missing flag as false", async () => {
    respond(200, { ...turn, usage_limited: true });
    const limited = await sendTestMessage("tok", { message: "hi" });
    expect(limited.kind === "ok" && limited.data.usage_limited).toBe(true);

    respond(200, turn);
    const plain = await sendTestMessage("tok", { message: "hi" });
    expect(plain.kind === "ok" && plain.data.usage_limited).toBe(false);
  });

  it("maps 429 concurrency_limited, and treats other 429s as unreachable", async () => {
    respond(429, error("concurrency_limited"));
    expect(await sendTestMessage("tok", { message: "hi" })).toEqual({ kind: "concurrency_limited" });
    respond(429, error("rate_limited"));
    expect((await sendTestMessage("tok", { message: "hi" })).kind).toBe("unreachable");
  });
});
