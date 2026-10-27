import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  associatePhoneNumber,
  disconnectPhoneNumber,
  getPhoneNumbers,
  testPhoneNumber,
  type PhoneNumber,
  type PhoneNumbers,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

const NUMBER: PhoneNumber = {
  id: "7d0c6a64-3c5e-4b8a-9d0e-0a8f4c2b1a11",
  phone_number: "+15551234567",
  provider_number_sid: "PN" + "a".repeat(32),
  status: "connected",
  last_error: null,
  verified_at: "2026-10-27T09:00:00Z",
  created_at: "2026-10-27T08:00:00Z",
};

const LIST: PhoneNumbers = {
  configured: true,
  trial: true,
  numbers: [NUMBER],
  available: [{ provider_number_sid: "PN" + "b".repeat(32), phone_number: "+15559876543", friendly_name: null }],
  available_error: null,
};

function respond(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const error = (code: string, message = code) => ({ error: { code, message } });

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
});

afterEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
  vi.unstubAllGlobals();
});

function sharedFailures(call: () => Promise<{ kind: string }>) {
  it("maps 401, 402 and the two 503 codes", async () => {
    respond(401, error("unauthorized"));
    expect(await call()).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await call()).toEqual({ kind: "subscription_required" });
    respond(503, error("telephony_not_configured"));
    expect(await call()).toEqual({ kind: "not_configured" });
    respond(503, error("telephony_unavailable"));
    expect(await call()).toEqual({ kind: "unavailable" });
  });

  it("reports an unreachable API when fetch throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });

  it("treats an unexpected status as unreachable", async () => {
    respond(500, error("internal"));
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });
}

describe("getPhoneNumbers", () => {
  it("parses the list and sends the bearer token", async () => {
    const fetchMock = respond(200, LIST);
    expect(await getPhoneNumbers("tok")).toEqual({ kind: "ok", data: LIST });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/v1/phone-numbers");
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("accepts an unknown trial flag and an available_error", async () => {
    const body = { ...LIST, trial: null, available: [], available_error: "unavailable" };
    expect(respond(200, body) && (await getPhoneNumbers("tok"))).toEqual({ kind: "ok", data: body });
  });

  it.each([
    ["missing configured", { ...LIST, configured: undefined }],
    ["bad trial", { ...LIST, trial: "yes" }],
    ["bad status", { ...LIST, numbers: [{ ...NUMBER, status: "weird" }] }],
    ["bad last_error", { ...LIST, numbers: [{ ...NUMBER, last_error: "boom" }] }],
    ["bad available", { ...LIST, available: [{ phone_number: "+1555" }] }],
    ["bad available_error", { ...LIST, available_error: "nope" }],
  ])("rejects a malformed payload: %s", async (_name, body) => {
    respond(200, body);
    expect(await getPhoneNumbers("tok")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => getPhoneNumbers("tok"));
});

describe("associatePhoneNumber", () => {
  it("posts the number SID and returns the created number on 201", async () => {
    const fetchMock = respond(201, NUMBER);
    expect(await associatePhoneNumber("tok", NUMBER.provider_number_sid)).toEqual({ kind: "ok", data: NUMBER });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ provider_number_sid: NUMBER.provider_number_sid });
  });

  it("rejects a malformed 201 body", async () => {
    respond(201, { ...NUMBER, phone_number: 5 });
    expect(await associatePhoneNumber("tok", "PN1")).toMatchObject({ kind: "unreachable" });
  });

  it("maps 409 phone_number_in_use and 404 phone_number_not_found", async () => {
    respond(409, error("phone_number_in_use"));
    expect(await associatePhoneNumber("tok", "PN1")).toEqual({ kind: "in_use" });
    respond(404, error("phone_number_not_found"));
    expect(await associatePhoneNumber("tok", "PN1")).toEqual({ kind: "not_found" });
  });

  sharedFailures(() => associatePhoneNumber("tok", "PN1"));
});

describe("testPhoneNumber", () => {
  it("posts to the test route and returns the refreshed number", async () => {
    const refreshed = { ...NUMBER, status: "misconfigured", last_error: "voice_url_mismatch", verified_at: null };
    const fetchMock = respond(200, refreshed);
    expect(await testPhoneNumber("tok", NUMBER.id)).toEqual({ kind: "ok", data: refreshed });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`https://api.example.test/api/v1/phone-numbers/${NUMBER.id}/test`);
    expect(init.method).toBe("POST");
  });

  it("maps 404 and rejects a malformed body", async () => {
    respond(404, error("phone_number_not_found"));
    expect(await testPhoneNumber("tok", "x")).toEqual({ kind: "not_found" });
    respond(200, { id: "x" });
    expect(await testPhoneNumber("tok", "x")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => testPhoneNumber("tok", "x"));
});

describe("disconnectPhoneNumber", () => {
  it("returns whether the webhooks were cleared", async () => {
    const fetchMock = respond(200, { webhooks_cleared: false });
    expect(await disconnectPhoneNumber("tok", NUMBER.id)).toEqual({ kind: "ok", webhooksCleared: false });
    expect((fetchMock.mock.calls[0] as [string, RequestInit])[1].method).toBe("DELETE");
    respond(200, { webhooks_cleared: true });
    expect(await disconnectPhoneNumber("tok", NUMBER.id)).toEqual({ kind: "ok", webhooksCleared: true });
  });

  it("maps 404 and rejects a body without webhooks_cleared", async () => {
    respond(404, error("phone_number_not_found"));
    expect(await disconnectPhoneNumber("tok", "x")).toEqual({ kind: "not_found" });
    respond(200, {});
    expect(await disconnectPhoneNumber("tok", "x")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => disconnectPhoneNumber("tok", "x"));
});
