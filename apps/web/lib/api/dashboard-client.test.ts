import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  activateAgent,
  deactivateAgent,
  deleteAccount,
  deleteTestData,
  getAgentReadiness,
  getDashboardSummary,
  getIntegrationStatus,
  type AccountDeletionResult,
  type AgentReadiness,
  type DashboardSummary,
  type IntegrationStatus,
  type TestDataReport,
  type UsageRead,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

const SUMMARY: DashboardSummary = {
  from: "2026-09-29",
  to: "2026-10-28",
  timezone: "America/New_York",
  total_interactions: 12,
  successful_bookings: 3,
  faqs_answered: 5,
  open_escalations: 2,
};

const READINESS: AgentReadiness = {
  active: false,
  ready: false,
  items: [
    {
      key: "persona",
      ok: true,
      required: true,
      message: "Name and greeting are set.",
      fix_url: "/dashboard/agent#persona",
    },
    {
      key: "services",
      ok: false,
      required: true,
      message: "Add at least one active service.",
      fix_url: "/dashboard/agent#services",
    },
  ],
};

const USAGE: UsageRead = {
  window: { timezone: "UTC", starts_at: "2026-10-28T00:00:00Z", resets_at: "2026-10-29T00:00:00Z" },
  meters: [{ kind: "gemini_request", used: 3, limit: 50, global_limit_reached: false }],
  gemini_tokens: 100,
  failed_requests: 0,
  estimated_cost_usd: "0.000100",
  concurrency: { active: 0, limit: 3, idle_minutes: 15, global_limit_reached: false },
  max_call_duration_seconds: 600,
};

const STATUS: IntegrationStatus = {
  calendar: {
    configured: true,
    status: "reconnect_needed",
    calendar_name: "Salon",
    last_error: "auth_revoked",
    last_synced_at: "2026-10-28T09:00:00Z",
  },
  phone: {
    configured: true,
    status: "connected",
    phone_number: "+15005550006",
    last_error: null,
    verified_at: "2026-10-28T08:00:00Z",
  },
  agent: { active: true, ready: true },
  readiness: { ...READINESS, active: true, ready: true },
  errors: [{ source: "calendar", code: "auth_revoked", occurred_at: "2026-10-28T09:00:00Z" }],
  usage: USAGE,
};

const REPORT: TestDataReport = {
  interactions: 12,
  messages: 80,
  bookings: 3,
  escalations: 2,
  usage_events: 40,
  calendar_events_cancelled: 2,
  calendar_events_not_cancelled: 1,
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

/** The outcomes every call shares: 401/402, thrown fetch, unreadable body, and an unexpected status. */
function sharedFailures(call: () => Promise<{ kind: string }>) {
  it("maps 401 and 402", async () => {
    respond(401, error("unauthorized"));
    expect(await call()).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await call()).toEqual({ kind: "subscription_required" });
  });

  it("reports an unreachable API when fetch throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });

  it("reports an unreachable API when the body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })));
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });

  it("treats an unexpected status or shape as unreachable", async () => {
    respond(500, error("internal"));
    expect(await call()).toMatchObject({ kind: "unreachable" });
    respond(200, { nope: true });
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });
}

describe("getDashboardSummary", () => {
  it("parses the summary and sends the bearer token with no query by default", async () => {
    const fetchMock = respond(200, SUMMARY);
    expect(await getDashboardSummary("tok")).toEqual({ kind: "ok", data: SUMMARY });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/v1/dashboard/summary");
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("sends the range as from and to", async () => {
    const fetchMock = respond(200, SUMMARY);
    await getDashboardSummary("tok", { from: "2026-10-01", to: "2026-10-28" });
    expect(fetchMock.mock.calls[0]![0]).toBe(
      "https://api.example.test/api/v1/dashboard/summary?from=2026-10-01&to=2026-10-28",
    );
  });

  it("returns the API message for an invalid range", async () => {
    respond(422, error("invalid_date_range", "From must not be after to."));
    expect(await getDashboardSummary("tok", { from: "2026-10-28", to: "2026-10-01" })).toEqual({
      kind: "validation",
      message: "From must not be after to.",
    });
  });

  it("rejects a negative count", async () => {
    respond(200, { ...SUMMARY, total_interactions: -1 });
    expect(await getDashboardSummary("tok")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => getDashboardSummary("tok"));
});

describe("getIntegrationStatus", () => {
  it("parses the status and sends the bearer token", async () => {
    const fetchMock = respond(200, STATUS);
    expect(await getIntegrationStatus("tok")).toEqual({ kind: "ok", data: STATUS });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/v1/integrations/status");
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("accepts unknown status, source and code strings", async () => {
    const body = {
      ...STATUS,
      calendar: { ...STATUS.calendar, status: "paused_by_provider" },
      phone: { ...STATUS.phone, status: "porting", phone_number: null },
      errors: [{ source: "whatsapp", code: "new_failure_code", occurred_at: "2026-10-28T09:00:00Z" }],
    };
    respond(200, body);
    expect(await getIntegrationStatus("tok")).toEqual({ kind: "ok", data: body });
  });

  it("reads an error without a time as occurred_at null", async () => {
    respond(200, { ...STATUS, errors: [{ source: "calendar", code: "reconnect_needed" }] });
    const result = await getIntegrationStatus("tok");
    expect(result).toMatchObject({
      kind: "ok",
      data: { errors: [{ source: "calendar", code: "reconnect_needed", occurred_at: null }] },
    });
  });

  it("rejects a readiness item whose fix link leaves the site", async () => {
    const readiness = {
      ...STATUS.readiness,
      items: [{ ...READINESS.items[0], fix_url: "https://evil.example/path" }],
    };
    respond(200, { ...STATUS, readiness });
    expect(await getIntegrationStatus("tok")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => getIntegrationStatus("tok"));
});

describe("getAgentReadiness", () => {
  it("parses the checklist", async () => {
    const fetchMock = respond(200, READINESS);
    expect(await getAgentReadiness("tok")).toEqual({ kind: "ok", data: READINESS });
    expect(fetchMock.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/agent/readiness");
  });

  it("accepts an item key it does not know", async () => {
    const body = {
      ...READINESS,
      items: [
        ...READINESS.items,
        { key: "brand_new", ok: false, required: false, message: "Do the thing.", fix_url: "/dashboard/agent" },
      ],
    };
    respond(200, body);
    expect(await getAgentReadiness("tok")).toEqual({ kind: "ok", data: body });
  });

  it.each([
    ["protocol-relative", "//evil.example"],
    ["tab before the second slash", "/\t/evil.example"],
    ["newline inside", "/dashboard\n//evil.example"],
    ["backslash", "/\\evil.example"],
  ])("rejects a %s fix link", async (_name, fixUrl) => {
    respond(200, { ...READINESS, items: [{ ...READINESS.items[0], fix_url: fixUrl }] });
    expect(await getAgentReadiness("tok")).toMatchObject({ kind: "unreachable" });
  });

  it("accepts a same-site fix link with an anchor", async () => {
    const body = { ...READINESS, items: [{ ...READINESS.items[0], fix_url: "/dashboard/agent#test-chat" }] };
    respond(200, body);
    expect(await getAgentReadiness("tok")).toEqual({ kind: "ok", data: body });
  });

  sharedFailures(() => getAgentReadiness("tok"));
});

describe("activateAgent", () => {
  it("posts and returns the readiness from the 200", async () => {
    const active = { ...READINESS, active: true, ready: true };
    const fetchMock = respond(200, active);
    expect(await activateAgent("tok")).toEqual({ kind: "ok", data: active });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/v1/agent/activate");
    expect(init.method).toBe("POST");
  });

  it("reports not_ready with the API message on a 409 agent_not_ready", async () => {
    respond(409, error("agent_not_ready", "Finish services before activating."));
    expect(await activateAgent("tok")).toEqual({
      kind: "not_ready",
      message: "Finish services before activating.",
    });
  });

  it("does not treat another 409 as not_ready", async () => {
    respond(409, error("something_else"));
    expect(await activateAgent("tok")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => activateAgent("tok"));
});

describe("deactivateAgent", () => {
  it("posts and returns the readiness from the 200", async () => {
    const off = { ...READINESS, active: false, ready: true };
    const fetchMock = respond(200, off);
    expect(await deactivateAgent("tok")).toEqual({ kind: "ok", data: off });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/v1/agent/deactivate");
    expect(init.method).toBe("POST");
  });

  sharedFailures(() => deactivateAgent("tok"));
});

describe("deleteTestData", () => {
  it("sends the confirmation as a DELETE body and returns the counts", async () => {
    const fetchMock = respond(200, REPORT);
    expect(await deleteTestData("tok", "DELETE TEST DATA")).toEqual({ kind: "ok", data: REPORT });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/v1/business/test-data");
    expect(init.method).toBe("DELETE");
    expect(init.headers).toEqual({ Authorization: "Bearer tok", "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({ confirmation: "DELETE TEST DATA" });
  });

  it("maps a 422 confirmation_mismatch", async () => {
    respond(422, error("confirmation_mismatch"));
    expect(await deleteTestData("tok", "nope")).toEqual({ kind: "confirmation_mismatch" });
  });

  it("does not report a result for a different 422", async () => {
    respond(422, error("validation_error"));
    expect(await deleteTestData("tok", "nope")).toMatchObject({ kind: "unreachable" });
  });

  it("does not report counts when one is missing", async () => {
    const { usage_events: _omit, ...partial } = REPORT;
    void _omit;
    respond(200, partial);
    expect(await deleteTestData("tok", "DELETE TEST DATA")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => deleteTestData("tok", "DELETE TEST DATA"));
});

describe("deleteAccount", () => {
  const RESULT: AccountDeletionResult = {
    calendar_events_cancelled: 2,
    calendar_events_not_cancelled: 1,
    calendar_token_revoked: true,
    sign_in_deleted: true,
  };

  it("sends the confirmation as a DELETE body and returns the API's result", async () => {
    const fetchMock = respond(200, RESULT);
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toEqual({ kind: "ok", data: RESULT });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/v1/account");
    expect(init.method).toBe("DELETE");
    expect(init.headers).toEqual({ Authorization: "Bearer tok", "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({ confirmation: "DELETE MY ACCOUNT" });
  });

  it("keeps sign_in_deleted: false as a successful deletion", async () => {
    respond(200, { ...RESULT, sign_in_deleted: false });
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toEqual({
      kind: "ok",
      data: { ...RESULT, sign_in_deleted: false },
    });
  });

  it("keeps calendar_token_revoked: null (no connection) as a successful deletion", async () => {
    respond(200, { ...RESULT, calendar_token_revoked: null });
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toEqual({
      kind: "ok",
      data: { ...RESULT, calendar_token_revoked: null },
    });
  });

  it("maps a 422 confirmation_mismatch", async () => {
    respond(422, error("confirmation_mismatch"));
    expect(await deleteAccount("tok", "nope")).toEqual({ kind: "confirmation_mismatch" });
  });

  it("maps a 503 billing_unreachable", async () => {
    respond(503, error("billing_unreachable"));
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toEqual({ kind: "billing_unreachable" });
  });

  it("does not report a result for a different 422 or 503", async () => {
    respond(422, error("validation_error"));
    expect(await deleteAccount("tok", "nope")).toMatchObject({ kind: "unreachable" });
    respond(503, error("service_unavailable"));
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toMatchObject({ kind: "unreachable" });
  });

  it("does not report a result when a field is missing or mistyped", async () => {
    const { sign_in_deleted: _omit, ...partial } = RESULT;
    void _omit;
    respond(200, partial);
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toMatchObject({ kind: "unreachable" });
    respond(200, { ...RESULT, calendar_token_revoked: "yes" });
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toMatchObject({ kind: "unreachable" });
  });

  it("maps 401", async () => {
    respond(401, error("unauthorized"));
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toEqual({ kind: "unauthorized" });
  });

  it("reports an unreachable API when fetch throws or the body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toMatchObject({ kind: "unreachable" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })));
    expect(await deleteAccount("tok", "DELETE MY ACCOUNT")).toMatchObject({ kind: "unreachable" });
  });
});
