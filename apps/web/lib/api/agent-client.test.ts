import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  endTestChat,
  getAgent,
  getTestTranscript,
  sendTestMessage,
  updateAgent,
  type AgentRead,
  type TranscriptRead,
  type TurnRead,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;
const ID = "22222222-2222-4222-8222-222222222222";

const AGENT: AgentRead = {
  id: "a1",
  name: "Riley",
  tone: "friendly",
  greeting: "Hi there!",
  custom_instructions: "",
  active: true,
  updated_at: "2026-01-01T00:00:00Z",
};

const MESSAGE = {
  sequence_number: 1,
  speaker: "agent" as const,
  text: "Hi there!",
  created_at: "2026-01-01T00:00:00Z",
  fallback: false,
};

const TURN: TurnRead = {
  interaction_id: ID,
  reply_text: "Sure.",
  intent: "UNKNOWN",
  status: "active",
  ended: false,
  fallback: false,
  messages: [MESSAGE],
};

const TRANSCRIPT: TranscriptRead = {
  interaction_id: ID,
  status: "ended",
  ended: true,
  started_at: "2026-01-01T00:00:00Z",
  ended_at: "2026-01-01T00:05:00Z",
  messages: [MESSAGE],
};

function respond(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const error = (code: string, message = code) => ({ error: { code, message } });

describe("agent client", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  describe("getAgent", () => {
    it("returns the agent with a bearer token", async () => {
      const fetchMock = respond(200, AGENT);
      expect(await getAgent("tok")).toEqual({ kind: "ok", data: AGENT });
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("https://api.example.test/api/v1/agent");
      expect(init.headers).toEqual({ Authorization: "Bearer tok" });
    });

    it("maps 401, 402 and 404", async () => {
      respond(401, error("unauthorized"));
      expect(await getAgent("tok")).toEqual({ kind: "unauthorized" });
      respond(402, error("subscription_required"));
      expect(await getAgent("tok")).toEqual({ kind: "subscription_required" });
      respond(404, error("business_not_found"));
      expect(await getAgent("tok")).toEqual({ kind: "not_found" });
    });

    it("is unreachable on a malformed body, a thrown fetch, or unreadable JSON", async () => {
      respond(200, { ...AGENT, tone: "sarcastic" });
      expect((await getAgent("tok")).kind).toBe("unreachable");

      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await getAgent("tok")).kind).toBe("unreachable");

      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })));
      expect((await getAgent("tok")).kind).toBe("unreachable");
    });
  });

  describe("updateAgent", () => {
    it("PATCHes only the given fields", async () => {
      const fetchMock = respond(200, { ...AGENT, name: "Sam" });
      const result = await updateAgent("tok", { name: "Sam" });
      expect(result).toEqual({ kind: "ok", data: { ...AGENT, name: "Sam" } });
      const [, init] = fetchMock.mock.calls[0]!;
      expect(init.method).toBe("PATCH");
      expect(JSON.parse(init.body)).toEqual({ name: "Sam" });
    });

    it("returns the message and any field errors on 422", async () => {
      respond(422, error("validation_error", "Name is too long."));
      expect(await updateAgent("tok", { name: "x" })).toEqual({
        kind: "validation",
        message: "Name is too long.",
        fieldErrors: {},
      });

      respond(422, {
        error: { code: "validation_error", message: "Invalid.", fields: { name: "Too long." } },
      });
      expect(await updateAgent("tok", { name: "x" })).toEqual({
        kind: "validation",
        message: "Invalid.",
        fieldErrors: { name: "Too long." },
      });
    });

    it("maps 401, 402, 404 and unreachable", async () => {
      respond(401, error("unauthorized"));
      expect(await updateAgent("tok", {})).toEqual({ kind: "unauthorized" });
      respond(402, error("subscription_required"));
      expect(await updateAgent("tok", {})).toEqual({ kind: "subscription_required" });
      respond(404, error("business_not_found"));
      expect(await updateAgent("tok", {})).toEqual({ kind: "not_found" });
      respond(500, { detail: "boom" });
      expect((await updateAgent("tok", {})).kind).toBe("unreachable");
    });
  });

  describe("sendTestMessage", () => {
    it("omits interaction_id on the first message", async () => {
      const fetchMock = respond(200, TURN);
      expect(await sendTestMessage("tok", { message: "Hello" })).toEqual({ kind: "ok", data: TURN });
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("https://api.example.test/api/v1/agent/test");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({ message: "Hello" });
    });

    it("sends interaction_id when continuing a chat", async () => {
      const fetchMock = respond(200, TURN);
      await sendTestMessage("tok", { interactionId: ID, message: "Hello" });
      expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({
        interaction_id: ID,
        message: "Hello",
      });
    });

    it("maps 409 by error code: ended vs busy", async () => {
      respond(409, error("interaction_ended"));
      expect(await sendTestMessage("tok", { message: "a" })).toEqual({ kind: "ended" });
      respond(409, error("interaction_busy"));
      expect(await sendTestMessage("tok", { message: "a" })).toEqual({ kind: "busy" });
    });

    it("treats a missing message fallback flag as false and keeps a true one", async () => {
      const { fallback: _omit, ...legacy } = MESSAGE;
      void _omit;
      respond(200, { ...TURN, messages: [legacy, { ...MESSAGE, sequence_number: 2, fallback: true }] });
      const result = await sendTestMessage("tok", { message: "a" });
      expect(result.kind === "ok" && result.data.messages.map((m) => m.fallback)).toEqual([false, true]);
    });

    it("maps the other statuses", async () => {
      respond(404, error("interaction_not_found"));
      expect(await sendTestMessage("tok", { message: "a" })).toEqual({ kind: "not_found" });
      respond(402, error("subscription_required"));
      expect(await sendTestMessage("tok", { message: "a" })).toEqual({ kind: "subscription_required" });
      respond(401, error("unauthorized"));
      expect(await sendTestMessage("tok", { message: "a" })).toEqual({ kind: "unauthorized" });
      respond(422, error("validation_error", "Too long."));
      expect(await sendTestMessage("tok", { message: "a" })).toEqual({
        kind: "validation",
        message: "Too long.",
        fieldErrors: {},
      });
    });

    it("rejects a malformed turn body", async () => {
      respond(200, { ...TURN, messages: [{ nope: true }] });
      expect((await sendTestMessage("tok", { message: "a" })).kind).toBe("unreachable");
    });
  });

  describe("getTestTranscript and endTestChat", () => {
    it("fetch the transcript and end the chat", async () => {
      const fetchMock = respond(200, TRANSCRIPT);
      expect(await getTestTranscript("tok", ID)).toEqual({ kind: "ok", data: TRANSCRIPT });
      expect(fetchMock.mock.calls[0]![0]).toBe(`https://api.example.test/api/v1/agent/test/${ID}`);

      const endMock = respond(200, TRANSCRIPT);
      expect(await endTestChat("tok", ID)).toEqual({ kind: "ok", data: TRANSCRIPT });
      const [url, init] = endMock.mock.calls[0]!;
      expect(url).toBe(`https://api.example.test/api/v1/agent/test/${ID}/end`);
      expect(init.method).toBe("POST");
    });

    it("treat 404 and a malformed id (422) as not_found, and map auth statuses", async () => {
      respond(404, error("interaction_not_found"));
      expect(await getTestTranscript("tok", ID)).toEqual({ kind: "not_found" });
      respond(422, error("validation_error"));
      expect(await getTestTranscript("tok", "nope")).toEqual({ kind: "not_found" });
      respond(404, error("interaction_not_found"));
      expect(await endTestChat("tok", ID)).toEqual({ kind: "not_found" });
      respond(402, error("subscription_required"));
      expect(await getTestTranscript("tok", ID)).toEqual({ kind: "subscription_required" });
      respond(401, error("unauthorized"));
      expect(await endTestChat("tok", ID)).toEqual({ kind: "unauthorized" });
    });

    it("are unreachable on a malformed body", async () => {
      respond(200, { interaction_id: ID });
      expect((await getTestTranscript("tok", ID)).kind).toBe("unreachable");
      expect((await endTestChat("tok", ID)).kind).toBe("unreachable");
    });
  });
});
