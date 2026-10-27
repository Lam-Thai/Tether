import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getEscalationSettings,
  getInteraction,
  listEscalations,
  listInteractions,
  resolveEscalation,
  updateEscalationSettings,
  type EscalationRead,
  type EscalationSettingsRead,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;
const ID = "11111111-1111-4111-8111-111111111111";

const SETTINGS: EscalationSettingsRead = {
  contact_phone: "+14155550123",
  sms_enabled: true,
  clarification_threshold: 3,
  urgent_keywords: ["flood"],
  customized: true,
  sms_configured: false,
};

const ESC: EscalationRead = {
  id: "e1",
  interaction_id: ID,
  reason: "complaint",
  urgency: "normal",
  status: "open",
  sms_status: "logged",
  sms_error: null,
  created_at: "2026-10-21T18:30:00Z",
  resolved_at: null,
};

const ROW = {
  id: ID,
  channel: "test_chat",
  started_at: "2026-10-21T18:30:00Z",
  ended_at: null,
  caller: null,
  intent: null,
  summary: null,
  summary_source: null,
  status: "in_progress",
  outcome: "escalated",
  requires_human: true,
  needs_attention: true,
  escalation: { id: "e1", reason: "complaint", urgency: "normal", status: "open", sms_status: "logged" },
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

describe("getEscalationSettings", () => {
  it("returns the settings", async () => {
    const fetchMock = respond(200, SETTINGS);
    expect(await getEscalationSettings("tok")).toEqual({ kind: "ok", data: SETTINGS });
    expect(fetchMock.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/business/escalation-settings");
    expect(fetchMock.mock.calls[0]![1].headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("maps 401, 402 and a malformed body", async () => {
    respond(401, error("unauthorized"));
    expect(await getEscalationSettings("tok")).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await getEscalationSettings("tok")).toEqual({ kind: "subscription_required" });
    respond(200, { ...SETTINGS, sms_configured: "yes" });
    expect((await getEscalationSettings("tok")).kind).toBe("unreachable");
  });

  it("is unreachable when fetch throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    expect((await getEscalationSettings("tok")).kind).toBe("unreachable");
  });
});

describe("updateEscalationSettings", () => {
  const body = {
    contact_phone: "+14155550123",
    sms_enabled: true,
    clarification_threshold: 3,
    urgent_keywords: ["flood"],
  };

  it("PUTs exactly the four fields", async () => {
    const fetchMock = respond(200, SETTINGS);
    expect(await updateEscalationSettings("tok", body)).toEqual({ kind: "ok", data: SETTINGS });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/business/escalation-settings");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual(body);
  });

  it("maps contact_phone_required apart from other 422s", async () => {
    respond(422, error("contact_phone_required", "Add a phone."));
    expect(await updateEscalationSettings("tok", body)).toEqual({
      kind: "contact_phone_required",
      message: "Add a phone.",
    });
    respond(422, error("validation_error", "Too many keywords."));
    expect(await updateEscalationSettings("tok", body)).toMatchObject({
      kind: "validation",
      message: "Too many keywords.",
    });
  });

  it("maps 401 and 402", async () => {
    respond(401, error("unauthorized"));
    expect(await updateEscalationSettings("tok", body)).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await updateEscalationSettings("tok", body)).toEqual({ kind: "subscription_required" });
  });
});

describe("listEscalations", () => {
  it("sends status and interaction_id and returns the items", async () => {
    const fetchMock = respond(200, { items: [ESC] });
    const result = await listEscalations("tok", { status: "all", interactionId: ID });
    expect(result).toEqual({ kind: "ok", data: [ESC] });
    expect(fetchMock.mock.calls[0]![0]).toBe(
      `https://api.example.test/api/v1/escalations?status=all&interaction_id=${ID}`,
    );
  });

  it("has no query string by default and rejects malformed items", async () => {
    const fetchMock = respond(200, { items: [] });
    await listEscalations("tok");
    expect(fetchMock.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/escalations");
    respond(200, { items: [{ ...ESC, status: "weird" }] });
    expect((await listEscalations("tok")).kind).toBe("unreachable");
  });

  it("keeps unknown reason and sms_status strings", async () => {
    respond(200, { items: [{ ...ESC, reason: "future_reason", sms_status: "future_status" }] });
    expect(await listEscalations("tok")).toMatchObject({ kind: "ok" });
  });
});

describe("resolveEscalation", () => {
  it("PATCHes status resolved", async () => {
    const resolved = { ...ESC, status: "resolved", resolved_at: "2026-10-21T19:00:00Z" };
    const fetchMock = respond(200, resolved);
    expect(await resolveEscalation("tok", "e1")).toEqual({ kind: "ok", data: resolved });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/escalations/e1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ status: "resolved" });
  });

  it("maps 404, 401 and 402", async () => {
    respond(404, error("escalation_not_found"));
    expect(await resolveEscalation("tok", "e1")).toEqual({ kind: "not_found" });
    respond(401, error("unauthorized"));
    expect(await resolveEscalation("tok", "e1")).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await resolveEscalation("tok", "e1")).toEqual({ kind: "subscription_required" });
  });
});

describe("inbox escalation fields", () => {
  it("sends type=attention and accepts rows with needs_attention and an escalation", async () => {
    const list = { items: [ROW], next_cursor: null, timezone: "UTC" };
    const fetchMock = respond(200, list);
    const result = await listInteractions("tok", { type: "attention" });
    expect(result).toEqual({ kind: "ok", data: list });
    expect(fetchMock.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/interactions?type=attention");
  });

  it("rejects a row without needs_attention", async () => {
    const { needs_attention: _omit, ...legacy } = ROW;
    void _omit;
    respond(200, { items: [legacy], next_cursor: null, timezone: "UTC" });
    expect((await listInteractions("tok")).kind).toBe("unreachable");
  });

  it("reads the detail's escalations", async () => {
    const detail = { ...ROW, timezone: "UTC", message_count: 1, operations: [], escalations: [ESC] };
    respond(200, detail);
    const result = await getInteraction("tok", ID);
    expect(result).toMatchObject({ kind: "ok", data: { escalations: [ESC] } });
    respond(200, { ...detail, escalations: [{ ...ESC, id: 5 }] });
    expect((await getInteraction("tok", ID)).kind).toBe("unreachable");
  });
});
