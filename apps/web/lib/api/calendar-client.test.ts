import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  completeCalendarConnection,
  disconnectCalendar,
  getCalendarConnectUrl,
  getCalendarStatus,
  listCalendars,
  selectCalendar,
  type CalendarStatus,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

const CONNECTED: CalendarStatus = {
  status: "connected",
  calendar_id: "cal-1",
  calendar_name: "Salon bookings",
  last_error: null,
  last_synced_at: "2026-10-22T09:00:00Z",
};

const GOOGLE_URL = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=abc";

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

/** The outcomes every calendar endpoint shares. */
function sharedFailures(call: () => Promise<{ kind: string }>) {
  it("maps 401, 402 and the two 503 codes", async () => {
    respond(401, error("unauthorized"));
    expect(await call()).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await call()).toEqual({ kind: "subscription_required" });
    respond(503, error("calendar_not_configured"));
    expect(await call()).toEqual({ kind: "not_configured" });
    respond(503, error("calendar_unavailable"));
    expect(await call()).toEqual({ kind: "unavailable" });
  });

  it("reports an unreachable API when fetch throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });

  it("reports an unreadable body as unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })));
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });

  it("does not trust an unexpected 200 body", async () => {
    respond(200, { nope: true });
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });
}

describe("getCalendarStatus", () => {
  it("returns the status with a bearer token", async () => {
    const fetchMock = respond(200, CONNECTED);
    expect(await getCalendarStatus("tok")).toEqual({ kind: "ok", data: CONNECTED });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/calendar/status");
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("rejects a status value it does not know", async () => {
    respond(200, { ...CONNECTED, status: "mystery" });
    expect(await getCalendarStatus("tok")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => getCalendarStatus("tok"));
});

describe("getCalendarConnectUrl", () => {
  it("returns a Google consent URL", async () => {
    const fetchMock = respond(200, { url: GOOGLE_URL });
    expect(await getCalendarConnectUrl("tok")).toEqual({ kind: "ok", url: GOOGLE_URL });
    expect(fetchMock.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/calendar/connect");
  });

  it.each([
    ["a non-Google host", "https://evil.example/o/oauth2/v2/auth"],
    ["a look-alike host", "https://accounts.google.com.evil.example/auth"],
    ["a Google subdomain", "https://x.accounts.google.com/auth"],
    ["plain http", "http://accounts.google.com/o/oauth2/v2/auth"],
    ["a javascript URL", "javascript:alert(1)"],
    ["not a URL", "not a url"],
  ])("refuses %s", async (_name, url) => {
    respond(200, { url });
    expect(await getCalendarConnectUrl("tok")).toEqual({ kind: "invalid_url" });
  });

  it("refuses a body without a url", async () => {
    respond(200, { url: 42 });
    expect(await getCalendarConnectUrl("tok")).toEqual({ kind: "invalid_url" });
  });

  // A 200 with a bad body is the "invalid_url" case above, so only the shared transport cases apply.
  it("maps 401, 402 and the two 503 codes", async () => {
    respond(401, error("unauthorized"));
    expect(await getCalendarConnectUrl("tok")).toEqual({ kind: "unauthorized" });
    respond(402, error("subscription_required"));
    expect(await getCalendarConnectUrl("tok")).toEqual({ kind: "subscription_required" });
    respond(503, error("calendar_not_configured"));
    expect(await getCalendarConnectUrl("tok")).toEqual({ kind: "not_configured" });
    respond(503, error("calendar_unavailable"));
    expect(await getCalendarConnectUrl("tok")).toEqual({ kind: "unavailable" });
  });

  it("reports an unreachable API when fetch throws or the body is unreadable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    expect(await getCalendarConnectUrl("tok")).toMatchObject({ kind: "unreachable" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>", { status: 200 })));
    expect(await getCalendarConnectUrl("tok")).toMatchObject({ kind: "unreachable" });
  });
});

describe("completeCalendarConnection", () => {
  it("posts code and state as JSON and returns the status", async () => {
    const fetchMock = respond(200, {
      ...CONNECTED,
      status: "needs_selection",
      calendar_id: null,
      calendar_name: null,
    });
    const result = await completeCalendarConnection("tok", "the-code", "the-state");
    expect(result).toMatchObject({ kind: "ok", data: { status: "needs_selection" } });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/calendar/callback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ code: "the-code", state: "the-state" });
    expect(init.headers).toEqual({ Authorization: "Bearer tok", "Content-Type": "application/json" });
  });

  it.each([
    ["invalid_oauth_state", "invalid_state"],
    ["calendar_scopes_missing", "scopes_missing"],
    ["calendar_grant_incomplete", "grant_incomplete"],
  ])("maps 400 %s", async (code, kind) => {
    respond(400, error(code));
    expect(await completeCalendarConnection("tok", "c", "s")).toEqual({ kind });
  });

  it("does not guess at an unknown 400", async () => {
    respond(400, error("something_else"));
    expect(await completeCalendarConnection("tok", "c", "s")).toMatchObject({ kind: "unreachable" });
  });

  sharedFailures(() => completeCalendarConnection("tok", "c", "s"));
});

describe("listCalendars", () => {
  const calendars = [
    { id: "cal-1", name: "Salon bookings", primary: true, time_zone: "Europe/London" },
    { id: "cal-2", name: "Personal", primary: false, time_zone: null },
  ];

  it("returns the calendars", async () => {
    const fetchMock = respond(200, { calendars });
    expect(await listCalendars("tok")).toEqual({ kind: "ok", calendars });
    expect(fetchMock.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/calendar/calendars");
  });

  it("returns an empty list as ok", async () => {
    respond(200, { calendars: [] });
    expect(await listCalendars("tok")).toEqual({ kind: "ok", calendars: [] });
  });

  it("rejects a malformed calendar", async () => {
    respond(200, { calendars: [{ id: "x" }] });
    expect(await listCalendars("tok")).toMatchObject({ kind: "unreachable" });
  });

  it("maps 404 and 409", async () => {
    respond(404, error("calendar_not_connected"));
    expect(await listCalendars("tok")).toEqual({ kind: "not_connected" });
    respond(409, error("calendar_reconnect_needed"));
    expect(await listCalendars("tok")).toEqual({ kind: "reconnect_needed" });
  });

  sharedFailures(() => listCalendars("tok"));
});

describe("selectCalendar", () => {
  it("patches the calendar id and returns the status", async () => {
    const fetchMock = respond(200, CONNECTED);
    expect(await selectCalendar("tok", "cal/1")).toEqual({ kind: "ok", data: CONNECTED });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/calendar");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ calendar_id: "cal/1" });
  });

  it("maps 404 variants, 409 and 422", async () => {
    respond(404, error("calendar_not_found"));
    expect(await selectCalendar("tok", "x")).toEqual({ kind: "calendar_not_found" });
    respond(404, error("calendar_not_connected"));
    expect(await selectCalendar("tok", "x")).toEqual({ kind: "not_connected" });
    respond(409, error("calendar_reconnect_needed"));
    expect(await selectCalendar("tok", "x")).toEqual({ kind: "reconnect_needed" });
    respond(422, error("validation_error"));
    expect(await selectCalendar("tok", "x")).toEqual({ kind: "calendar_not_found" });
  });

  sharedFailures(() => selectCalendar("tok", "x"));
});

describe("disconnectCalendar", () => {
  it.each([true, false])("returns revoked=%s from the API", async (revoked) => {
    const fetchMock = respond(200, { revoked });
    expect(await disconnectCalendar("tok")).toEqual({ kind: "ok", revoked });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/calendar");
    expect(init.method).toBe("DELETE");
  });

  it("does not assume a revoke happened when the flag is missing", async () => {
    respond(200, {});
    expect(await disconnectCalendar("tok")).toMatchObject({ kind: "unreachable" });
  });

  it("maps 404 to not_connected", async () => {
    respond(404, error("calendar_not_connected"));
    expect(await disconnectCalendar("tok")).toEqual({ kind: "not_connected" });
  });

  sharedFailures(() => disconnectCalendar("tok"));
});
