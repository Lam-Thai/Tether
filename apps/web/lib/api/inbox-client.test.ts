import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getInteraction,
  listInteractionMessages,
  listInteractions,
  type InteractionDetail,
  type InteractionList,
  type InteractionRow,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;
const ID = "11111111-1111-4111-8111-111111111111";

const ROW: InteractionRow = {
  id: ID,
  channel: "test_chat",
  started_at: "2026-10-21T18:30:00Z",
  ended_at: "2026-10-21T18:34:00Z",
  caller: null,
  intent: "BOOK_APPOINTMENT",
  summary: "The customer asked to book a haircut. The appointment was booked for Fri 30 Oct at 3:30 PM.",
  summary_source: "ai",
  status: "completed",
  outcome: "booked",
  requires_human: false,
  needs_attention: false,
  escalation: null,
};

const LIST: InteractionList = { items: [ROW], next_cursor: "abc", timezone: "America/New_York" };

const DETAIL: InteractionDetail = {
  ...ROW,
  timezone: "America/New_York",
  message_count: 4,
  operations: [
    {
      id: "op1",
      tool_name: "create_booking",
      status: "succeeded",
      label: "booking 1a2b3c4d",
      created_at: "2026-10-21T18:33:00Z",
      duration_ms: 120,
    },
  ],
  escalations: [],
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

describe("listInteractions", () => {
  it("sends the filters it was given and omits empty ones and type=all", async () => {
    const fetchMock = respond(200, LIST);
    const result = await listInteractions("tok", {
      type: "all",
      status: "",
      from: "2026-10-01",
      to: "2026-10-31",
      cursor: "abc",
    });
    expect(result).toEqual({ kind: "ok", data: LIST });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/interactions?from=2026-10-01&to=2026-10-31&cursor=abc");
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("sends a type tab and no query string when there are no filters", async () => {
    const fetchMock = respond(200, LIST);
    await listInteractions("tok", { type: "failed" });
    expect(fetchMock.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/interactions?type=failed");
    await listInteractions("tok");
    expect(fetchMock.mock.calls[1]![0]).toBe("https://api.example.test/api/v1/interactions");
  });

  it("accepts an empty page", async () => {
    const data = { items: [], next_cursor: null, timezone: "UTC" };
    respond(200, data);
    expect(await listInteractions("tok")).toEqual({ kind: "ok", data });
  });

  it("maps 401, 402 and validation errors", async () => {
    respond(401, error("unauthorized"));
    expect(await listInteractions("tok")).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await listInteractions("tok")).toEqual({ kind: "subscription_required" });
    respond(400, error("invalid_cursor", "That page link is no longer valid."));
    expect(await listInteractions("tok")).toEqual({
      kind: "validation",
      message: "That page link is no longer valid.",
    });
    respond(422, error("validation_error", "from must be on or before to"));
    expect(await listInteractions("tok")).toEqual({
      kind: "validation",
      message: "from must be on or before to",
    });
  });

  it("is unreachable on a thrown fetch, a 500 and a malformed body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
    expect((await listInteractions("tok")).kind).toBe("unreachable");

    respond(500, error("internal"));
    expect((await listInteractions("tok")).kind).toBe("unreachable");

    respond(200, { items: [{ ...ROW, status: "active" }], next_cursor: null, timezone: "UTC" });
    expect((await listInteractions("tok")).kind).toBe("unreachable");

    respond(200, { items: [ROW], next_cursor: null });
    expect((await listInteractions("tok")).kind).toBe("unreachable");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })));
    expect((await listInteractions("tok")).kind).toBe("unreachable");
  });
});

describe("getInteraction", () => {
  it("returns the detail", async () => {
    const fetchMock = respond(200, DETAIL);
    expect(await getInteraction("tok", ID)).toEqual({ kind: "ok", data: DETAIL });
    expect(fetchMock.mock.calls[0]![0]).toBe(`https://api.example.test/api/v1/interactions/${ID}`);
  });

  it("maps 401, 402 and 404", async () => {
    respond(401, error("unauthorized"));
    expect(await getInteraction("tok", ID)).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await getInteraction("tok", ID)).toEqual({ kind: "subscription_required" });
    respond(404, error("interaction_not_found"));
    expect(await getInteraction("tok", ID)).toEqual({ kind: "not_found" });
  });

  it("is unreachable on a thrown fetch and on a malformed detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
    expect((await getInteraction("tok", ID)).kind).toBe("unreachable");

    respond(200, { ...DETAIL, operations: [{ ...DETAIL.operations[0], status: "weird" }] });
    expect((await getInteraction("tok", ID)).kind).toBe("unreachable");

    const { timezone: _timezone, ...noTimezone } = DETAIL;
    void _timezone;
    respond(200, noTimezone);
    expect((await getInteraction("tok", ID)).kind).toBe("unreachable");
  });
});

describe("listInteractionMessages", () => {
  const PAGE = {
    items: [
      { sequence_number: 1, speaker: "agent", text: "Hi.", created_at: "2026-10-21T18:30:00Z", fallback: false },
      { sequence_number: 2, speaker: "customer", text: "Hello", created_at: "2026-10-21T18:30:10Z", fallback: false },
    ],
    next_after: 2,
  };

  it("requests after the given sequence and defaults to 0", async () => {
    const fetchMock = respond(200, PAGE);
    expect(await listInteractionMessages("tok", ID, 40)).toEqual({ kind: "ok", data: PAGE });
    expect(fetchMock.mock.calls[0]![0]).toBe(
      `https://api.example.test/api/v1/interactions/${ID}/messages?after=40`,
    );
    await listInteractionMessages("tok", ID);
    expect(fetchMock.mock.calls[1]![0]).toBe(
      `https://api.example.test/api/v1/interactions/${ID}/messages?after=0`,
    );
  });

  it("reads a missing fallback flag as false", async () => {
    const { fallback: _fallback, ...bare } = PAGE.items[0]!;
    void _fallback;
    respond(200, { items: [bare], next_after: null });
    const result = await listInteractionMessages("tok", ID);
    expect(result.kind === "ok" && result.data.items[0]!.fallback).toBe(false);
  });

  it("maps 401, 402 and 404", async () => {
    respond(401, error("unauthorized"));
    expect(await listInteractionMessages("tok", ID)).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await listInteractionMessages("tok", ID)).toEqual({ kind: "subscription_required" });
    respond(404, error("interaction_not_found"));
    expect(await listInteractionMessages("tok", ID)).toEqual({ kind: "not_found" });
  });

  it("is unreachable on a thrown fetch and a malformed page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
    expect((await listInteractionMessages("tok", ID)).kind).toBe("unreachable");
    respond(200, { items: [{ sequence_number: "1" }], next_after: null });
    expect((await listInteractionMessages("tok", ID)).kind).toBe("unreachable");
    respond(200, { items: [], next_after: "2" });
    expect((await listInteractionMessages("tok", ID)).kind).toBe("unreachable");
  });
});
