import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createBusiness,
  getBusiness,
  getHealth,
  getMe,
  updateBusiness,
  type BusinessRead,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

const BUSINESS: BusinessRead = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Acme Dental",
  industry: "Dental",
  address: "123 Main St",
  timezone: "America/New_York",
  phone: "+15551234567",
  contact_email: "owner@acme.test",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

describe("getHealth", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  it("returns healthy when the API responds 200 with status ok / db connected", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", db: "connected", version: "1.2.3" }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result).toEqual({
      kind: "healthy",
      data: { status: "ok", db: "connected", version: "1.2.3" },
    });
    expect(fetchMock).toHaveBeenCalledWith("https://api.example.test/health", {
      cache: "no-store",
      signal: expect.any(AbortSignal),
    });
  });

  it("returns degraded when the API responds 503 with status degraded / db error", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "degraded", db: "error", version: "1.2.3" }), {
        status: 503,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result).toEqual({
      kind: "degraded",
      data: { status: "degraded", db: "error", version: "1.2.3" },
    });
  });

  it("returns unreachable when fetch itself throws (network failure)", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result.kind).toBe("unreachable");
  });

  it("returns unreachable when the response body isn't valid JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("not json", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result.kind).toBe("unreachable");
  });

  it("returns unreachable when the response body doesn't match the health contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ unexpected: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result.kind).toBe("unreachable");
  });

  it("returns unreachable when a 200 response carries a degraded-looking body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "degraded", db: "error", version: "1.2.3" }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result.kind).toBe("unreachable");
  });

  it("returns unreachable when a non-200/503 status carries a healthy-looking body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", db: "connected", version: "1.2.3" }), {
        status: 500,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result.kind).toBe("unreachable");
  });

  it("returns unreachable when NEXT_PUBLIC_API_BASE_URL is not configured", async () => {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result.kind).toBe("unreachable");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("getMe", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  it("returns ok with the caller's business when GET /me responds 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "u1", email: "owner@acme.test", business: BUSINESS }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMe("token-123");

    expect(result).toEqual({
      kind: "ok",
      data: { id: "u1", email: "owner@acme.test", business: BUSINESS },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/me",
      expect.objectContaining({
        headers: { Authorization: "Bearer token-123" },
      }),
    );
  });

  it("returns ok with business: null before onboarding", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "u1", email: "owner@acme.test", business: null }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMe("token-123");

    expect(result).toEqual({
      kind: "ok",
      data: { id: "u1", email: "owner@acme.test", business: null },
    });
  });

  it("returns unauthorized on a 401", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "unauthorized", message: "no" } }), {
        status: 401,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMe("bad-token");

    expect(result).toEqual({ kind: "unauthorized" });
  });

  it("returns unreachable when fetch throws", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMe("token-123");

    expect(result.kind).toBe("unreachable");
  });

  it("returns unreachable when the response body isn't valid JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("not json", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMe("token-123");

    expect(result.kind).toBe("unreachable");
  });

  it("returns unreachable when NEXT_PUBLIC_API_BASE_URL is not configured", async () => {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMe("token-123");

    expect(result.kind).toBe("unreachable");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("getBusiness", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  it("returns ok when GET /business responds 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(BUSINESS), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getBusiness("token-123");

    expect(result).toEqual({ kind: "ok", data: BUSINESS });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/business",
      expect.objectContaining({ headers: { Authorization: "Bearer token-123" } }),
    );
  });

  it("returns not_found on a 404", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: "business_not_found", message: "no business" } }),
        { status: 404 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getBusiness("token-123");

    expect(result).toEqual({ kind: "not_found" });
  });

  it("returns unauthorized on a 401", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "unauthorized", message: "no" } }), {
        status: 401,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getBusiness("bad-token");

    expect(result).toEqual({ kind: "unauthorized" });
  });

  it("returns unreachable when fetch throws", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getBusiness("token-123");

    expect(result.kind).toBe("unreachable");
  });
});

describe("createBusiness", () => {
  const input = {
    name: "Acme Dental",
    industry: "Dental",
    address: "123 Main St",
    timezone: "America/New_York",
    phone: null,
    contact_email: null,
  };

  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  it("returns ok when POST /business responds 201", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(BUSINESS), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createBusiness("token-123", input);

    expect(result).toEqual({ kind: "ok", data: BUSINESS });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/business",
      expect.objectContaining({
        method: "POST",
        headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    );
  });

  it("returns already_exists on a 409", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: "business_already_exists", message: "exists" } }),
        { status: 409 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createBusiness("token-123", input);

    expect(result).toEqual({ kind: "already_exists" });
  });

  it("returns invalid with the API's message on a 422", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: "validation_error", message: "bad timezone" } }),
        { status: 422 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createBusiness("token-123", { ...input, timezone: "Nowhere/Fake" });

    expect(result).toEqual({ kind: "invalid", message: "bad timezone" });
  });

  it("returns unauthorized on a 401", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "unauthorized", message: "no" } }), {
        status: 401,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createBusiness("bad-token", input);

    expect(result).toEqual({ kind: "unauthorized" });
  });

  it("returns unreachable when fetch throws", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createBusiness("token-123", input);

    expect(result.kind).toBe("unreachable");
  });
});

describe("updateBusiness", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  it("returns ok when PATCH /business/{id} responds 200", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(BUSINESS), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateBusiness("token-123", BUSINESS.id, { name: "New name" });

    expect(result).toEqual({ kind: "ok", data: BUSINESS });
    expect(fetchMock).toHaveBeenCalledWith(
      `https://api.example.test/api/v1/business/${BUSINESS.id}`,
      expect.objectContaining({
        method: "PATCH",
        headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
        body: JSON.stringify({ name: "New name" }),
      }),
    );
  });

  it("returns not_found on a 404 (id doesn't belong to the caller)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: "business_not_found", message: "no business" } }),
        { status: 404 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateBusiness("token-123", "not-mine", { name: "New name" });

    expect(result).toEqual({ kind: "not_found" });
  });

  it("returns invalid with the API's message on a 422", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: "validation_error", message: "bad timezone" } }),
        { status: 422 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateBusiness("token-123", BUSINESS.id, { timezone: "Nowhere/Fake" });

    expect(result).toEqual({ kind: "invalid", message: "bad timezone" });
  });

  it("returns unreachable when fetch throws", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);

    const result = await updateBusiness("token-123", BUSINESS.id, { name: "New name" });

    expect(result.kind).toBe("unreachable");
  });
});
