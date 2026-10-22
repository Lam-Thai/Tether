import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createService,
  deleteService,
  getBusinessHours,
  listServices,
  putBusinessHours,
  updateService,
  type DayHours,
  type ServiceRead,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;
const ID = "44444444-4444-4444-8444-444444444444";

const SERVICE: ServiceRead = {
  id: ID,
  name: "Cleaning",
  description: null,
  duration_minutes: 45,
  price: "45.00",
  active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const WEEK: DayHours[] = Array.from({ length: 7 }, (_, i) =>
  i < 5
    ? { day_of_week: i, is_closed: false, opens_at: "09:00:00", closes_at: "17:00:00" }
    : { day_of_week: i, is_closed: true, opens_at: null, closes_at: null },
);

function respond(status: number, body?: unknown) {
  const response =
    body === undefined ? new Response(null, { status }) : new Response(JSON.stringify(body), { status });
  const fetchMock = vi
    .fn<(url: string, init: RequestInit & { headers: unknown; method: string; body: string }) => Promise<Response>>()
    .mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const error = (code: string, message = code) => ({ error: { code, message } });

describe("services and hours client", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  describe("listServices", () => {
    it("returns services, currency and cap with the bearer token", async () => {
      const fetchMock = respond(200, { services: [SERVICE], currency: "USD", max_services: 50 });
      const result = await listServices("tok");

      expect(result).toEqual({
        kind: "ok",
        data: { services: [SERVICE], currency: "USD", max_services: 50 },
      });
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("https://api.example.test/api/v1/services");
      expect(init.headers).toEqual({ Authorization: "Bearer tok" });
    });

    it("maps 401 and 402", async () => {
      respond(401, error("unauthorized"));
      expect(await listServices("t")).toEqual({ kind: "unauthorized" });
      respond(402, error("subscription_required"));
      expect(await listServices("t")).toEqual({ kind: "subscription_required" });
    });

    it("is unreachable on a thrown fetch, malformed JSON or a wrong shape", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await listServices("t")).kind).toBe("unreachable");

      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json", { status: 200 })));
      expect((await listServices("t")).kind).toBe("unreachable");

      respond(200, { services: [{ ...SERVICE, price: 45 }], currency: "USD", max_services: 50 });
      expect((await listServices("t")).kind).toBe("unreachable");
    });
  });

  describe("createService", () => {
    it("POSTs the body and returns the 201", async () => {
      const fetchMock = respond(201, SERVICE);
      const input = { name: "Cleaning", description: null, duration_minutes: 45, price: "45.00", active: true };
      const result = await createService("tok", input);

      expect(result).toEqual({ kind: "ok", data: SERVICE });
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("https://api.example.test/api/v1/services");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual(input);
    });

    it("separates the two 409 codes", async () => {
      const input = { name: "A", duration_minutes: 5 };
      respond(409, error("service_limit_reached"));
      expect(await createService("t", input)).toEqual({ kind: "limit_reached" });
      respond(409, error("service_name_taken"));
      expect(await createService("t", input)).toEqual({ kind: "name_taken" });
      respond(409, error("other"));
      expect((await createService("t", input)).kind).toBe("unreachable");
    });

    it("maps 422, 401 and 402", async () => {
      const input = { name: "A", duration_minutes: 5 };
      respond(422, error("validation_error", "Bad price."));
      expect(await createService("t", input)).toEqual({
        kind: "validation",
        message: "Bad price.",
        fieldErrors: {},
      });
      respond(401, error("unauthorized"));
      expect(await createService("t", input)).toEqual({ kind: "unauthorized" });
      respond(402, error("subscription_required"));
      expect(await createService("t", input)).toEqual({ kind: "subscription_required" });
    });

    it("is unreachable on a thrown fetch", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await createService("t", { name: "A", duration_minutes: 5 })).kind).toBe("unreachable");
    });
  });

  describe("updateService", () => {
    it("PATCHes only the given fields", async () => {
      const fetchMock = respond(200, { ...SERVICE, price: null });
      const result = await updateService("tok", ID, { price: null });

      expect(result.kind).toBe("ok");
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe(`https://api.example.test/api/v1/services/${ID}`);
      expect(init.method).toBe("PATCH");
      expect(JSON.parse(init.body)).toEqual({ price: null });
    });

    it("maps 404, 409 name taken and 422", async () => {
      respond(404, error("service_not_found"));
      expect(await updateService("t", ID, {})).toEqual({ kind: "not_found" });
      respond(409, error("service_name_taken"));
      expect(await updateService("t", ID, { name: "x" })).toEqual({ kind: "name_taken" });
      respond(422, error("validation_error"));
      expect((await updateService("t", ID, {})).kind).toBe("validation");
    });
  });

  describe("deleteService", () => {
    it("treats only 204 as success", async () => {
      const fetchMock = respond(204);
      expect(await deleteService("tok", ID)).toEqual({ kind: "ok" });
      expect(fetchMock.mock.calls[0]![1].method).toBe("DELETE");

      respond(200, {});
      expect((await deleteService("t", ID)).kind).toBe("unreachable");
      respond(404, error("service_not_found"));
      expect(await deleteService("t", ID)).toEqual({ kind: "not_found" });
    });
  });

  describe("getBusinessHours", () => {
    it("returns the week", async () => {
      const fetchMock = respond(200, { timezone: "America/New_York", configured: true, days: WEEK });
      const result = await getBusinessHours("tok");

      expect(result).toEqual({
        kind: "ok",
        data: { timezone: "America/New_York", configured: true, days: WEEK },
      });
      expect(fetchMock.mock.calls[0]![0]).toBe("https://api.example.test/api/v1/business/hours");
    });

    it("returns an unconfigured week with no days", async () => {
      respond(200, { timezone: "UTC", configured: false, days: [] });
      expect(await getBusinessHours("t")).toEqual({
        kind: "ok",
        data: { timezone: "UTC", configured: false, days: [] },
      });
    });

    it("maps 401/402 and is unreachable on a bad shape or thrown fetch", async () => {
      respond(401, error("unauthorized"));
      expect(await getBusinessHours("t")).toEqual({ kind: "unauthorized" });
      respond(402, error("subscription_required"));
      expect(await getBusinessHours("t")).toEqual({ kind: "subscription_required" });
      respond(200, { timezone: "UTC", configured: true, days: [{ day_of_week: 9 }] });
      expect((await getBusinessHours("t")).kind).toBe("unreachable");
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await getBusinessHours("t")).kind).toBe("unreachable");
    });
  });

  describe("putBusinessHours", () => {
    it("PUTs exactly the days it is given", async () => {
      const fetchMock = respond(200, { timezone: "UTC", configured: true, days: WEEK });
      const result = await putBusinessHours("tok", WEEK);

      expect(result.kind).toBe("ok");
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("https://api.example.test/api/v1/business/hours");
      expect(init.method).toBe("PUT");
      expect(JSON.parse(init.body)).toEqual({ days: WEEK });
    });

    it("maps 422, 401 and 402", async () => {
      respond(422, error("validation_error", "Opens must be before closes."));
      expect(await putBusinessHours("t", WEEK)).toEqual({
        kind: "validation",
        message: "Opens must be before closes.",
        fieldErrors: {},
      });
      respond(401, error("unauthorized"));
      expect(await putBusinessHours("t", WEEK)).toEqual({ kind: "unauthorized" });
      respond(402, error("subscription_required"));
      expect(await putBusinessHours("t", WEEK)).toEqual({ kind: "subscription_required" });
    });

    it("is unreachable on malformed JSON", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 200 })));
      expect((await putBusinessHours("t", WEEK)).kind).toBe("unreachable");
    });
  });
});
