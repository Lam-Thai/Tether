import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAvailability, getBookingRules, putBookingRules, type Availability } from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

const RULES = {
  slot_interval_minutes: 15,
  min_notice_minutes: 120,
  max_days_ahead: 60,
  buffer_minutes: 0,
  customized: false,
};

const AVAILABLE: Availability = {
  status: "available",
  date: "2026-10-23",
  timezone: "America/New_York",
  service: { id: "svc-1", name: "Haircut", duration_minutes: 45 },
  slots: [{ start: "2026-10-23T14:00:00-04:00", end: "2026-10-23T14:45:00-04:00", label: "14:00" }],
  offered: [{ start: "2026-10-23T14:00:00-04:00", end: "2026-10-23T14:45:00-04:00", label: "14:00" }],
  reason: null,
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

  it("does not trust an unexpected 200 body", async () => {
    respond(200, { nope: true });
    expect(await call()).toMatchObject({ kind: "unreachable" });
  });
}

describe("getBookingRules", () => {
  it("returns the rules with the customized flag", async () => {
    const fetchMock = respond(200, RULES);
    expect(await getBookingRules("tok")).toEqual({ kind: "ok", data: RULES });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/business/booking-rules");
    expect((init as RequestInit).headers).toMatchObject({ Authorization: "Bearer tok" });
  });

  sharedFailures(() => getBookingRules("tok"));
});

describe("putBookingRules", () => {
  const BODY = { slot_interval_minutes: 30, min_notice_minutes: 60, max_days_ahead: 30, buffer_minutes: 10 };

  it("sends exactly the four integers and returns the saved rules", async () => {
    const saved = { ...BODY, customized: true };
    const fetchMock = respond(200, saved);
    expect(await putBookingRules("tok", { ...BODY, customized: true } as typeof BODY)).toEqual({
      kind: "ok",
      data: saved,
    });
    const [, init] = fetchMock.mock.calls[0]!;
    expect((init as RequestInit).method).toBe("PUT");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(BODY);
  });

  it("maps 422 to a validation result with the API message", async () => {
    respond(422, error("validation_error", "buffer_minutes must be at most 240"));
    expect(await putBookingRules("tok", BODY)).toMatchObject({
      kind: "validation",
      message: "buffer_minutes must be at most 240",
    });
  });

  sharedFailures(() => putBookingRules("tok", BODY));
});

describe("getAvailability", () => {
  it("returns an available day and puts the service and date in the query", async () => {
    const fetchMock = respond(200, AVAILABLE);
    expect(await getAvailability("tok", "svc-1", "2026-10-23")).toEqual({ kind: "ok", data: AVAILABLE });
    const [url] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/calendar/availability?service_id=svc-1&date=2026-10-23");
  });

  it.each(["closed", "hours_not_set", "fully_booked", "no_time_left", "past_date", "beyond_horizon", "service_too_long", "too_soon"] as const)(
    "accepts a no_availability day with reason %s",
    async (reason) => {
      const body = { ...AVAILABLE, status: "no_availability", slots: [], offered: [], reason };
      respond(200, body);
      expect(await getAvailability("tok", "svc-1", "2026-10-23")).toEqual({ kind: "ok", data: body });
    },
  );

  it.each(["not_connected", "reconnect_needed", "unavailable"] as const)(
    "accepts a calendar_unavailable day with reason %s",
    async (reason) => {
      const body = { ...AVAILABLE, status: "calendar_unavailable", service: null, slots: [], offered: [], reason };
      respond(200, body);
      expect(await getAvailability("tok", "svc-1", "2026-10-23")).toEqual({ kind: "ok", data: body });
    },
  );

  it("rejects an unknown status or reason instead of trusting it", async () => {
    respond(200, { ...AVAILABLE, status: "maybe" });
    expect(await getAvailability("tok", "svc-1", "2026-10-23")).toMatchObject({ kind: "unreachable" });
    respond(200, { ...AVAILABLE, reason: "because" });
    expect(await getAvailability("tok", "svc-1", "2026-10-23")).toMatchObject({ kind: "unreachable" });
  });

  it("maps 404 service_not_found and 422", async () => {
    respond(404, error("service_not_found"));
    expect(await getAvailability("tok", "svc-1", "2026-10-23")).toEqual({ kind: "service_not_found" });
    respond(422, error("validation_error", "date is invalid"));
    expect(await getAvailability("tok", "svc-1", "nope")).toEqual({ kind: "validation", message: "date is invalid" });
  });

  sharedFailures(() => getAvailability("tok", "svc-1", "2026-10-23"));
});
