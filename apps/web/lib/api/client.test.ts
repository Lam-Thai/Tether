import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createBusiness,
  getBillingPlan,
  openBillingPortal,
  startCheckout,
  syncCheckout,
  type SubscriptionRead,
  getBusiness,
  getHealth,
  getMe,
  updateBusiness,
  type BusinessRead,
} from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

const SUBSCRIPTION: SubscriptionRead = {
  entitled: true,
  status: "trialing",
  trial_ends_at: "2026-11-03T00:00:00Z",
  current_period_end: "2026-11-03T00:00:00Z",
  cancel_at_period_end: false,
  trial_available: false,
};

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
      new Response(JSON.stringify({ id: "u1", email: "owner@acme.test", business: BUSINESS, subscription: SUBSCRIPTION }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMe("token-123");

    expect(result).toEqual({
      kind: "ok",
      data: { id: "u1", email: "owner@acme.test", business: BUSINESS, subscription: SUBSCRIPTION },
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
      new Response(JSON.stringify({ id: "u1", email: "owner@acme.test", business: null, subscription: null }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMe("token-123");

    expect(result).toEqual({
      kind: "ok",
      data: { id: "u1", email: "owner@acme.test", business: null, subscription: null },
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

describe("billing client", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  function respond(status: number, body: unknown) {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  const error = (code: string) => ({ error: { code, message: code } });

  it("startCheckout posts the choice and returns the Stripe URL", async () => {
    const fetchMock = respond(200, { url: "https://checkout.stripe.com/c/pay/cs_test_1" });

    const result = await startCheckout("tok", "trial");

    expect(result).toEqual({ kind: "ok", url: "https://checkout.stripe.com/c/pay/cs_test_1" });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/billing/checkout");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ choice: "trial" });
  });

  it("startCheckout only follows redirects to Stripe's hosted pages", async () => {
    for (const url of [
      "javascript:alert(1)",
      "http://checkout.stripe.com/c/pay/x",
      "https://evil.example/checkout.stripe.com",
      "https://checkout.stripe.com.evil.example/c/pay/x",
    ]) {
      respond(200, { url });
      expect((await startCheckout("tok", "paid")).kind, url).toBe("unreachable");
    }
  });

  it("startCheckout reports a busy billing row so the owner can retry", async () => {
    respond(409, error("billing_busy"));
    expect(await startCheckout("tok", "paid")).toEqual({ kind: "busy" });
  });

  it("startCheckout maps the two 409s by error code", async () => {
    respond(409, error("trial_already_used"));
    expect(await startCheckout("tok", "trial")).toEqual({ kind: "trial_already_used" });

    respond(409, error("subscription_active"));
    expect(await startCheckout("tok", "paid")).toEqual({ kind: "subscription_active" });
  });

  it("startCheckout maps 404 to no_business and 401 to unauthorized", async () => {
    respond(404, error("business_not_found"));
    expect(await startCheckout("tok", "trial")).toEqual({ kind: "no_business" });

    respond(401, error("unauthorized"));
    expect(await startCheckout("tok", "trial")).toEqual({ kind: "unauthorized" });
  });

  it("syncCheckout url-encodes the session id and treats 422 as not found", async () => {
    const fetchMock = respond(422, error("validation_error"));

    const result = await syncCheckout("tok", "cs_test/../x");

    expect(result).toEqual({ kind: "not_found" });
    expect(fetchMock.mock.calls[0]![0]).toBe(
      "https://api.example.test/api/v1/billing/checkout/cs_test%2F..%2Fx/sync",
    );
  });

  it("syncCheckout returns the subscription view", async () => {
    respond(200, SUBSCRIPTION);
    expect(await syncCheckout("tok", "cs_test_1")).toEqual({ kind: "ok", data: SUBSCRIPTION });
  });

  it("getBillingPlan returns the plan and rejects a malformed body", async () => {
    const plan = { amount: 2900, currency: "usd", interval: "month", trial_days: 14, trial_available: true };
    respond(200, plan);
    expect(await getBillingPlan("tok")).toEqual({ kind: "ok", data: plan });

    respond(200, { amount: "29" });
    expect((await getBillingPlan("tok")).kind).toBe("unreachable");
  });

  it("openBillingPortal returns the portal URL or not_found", async () => {
    respond(200, { url: "https://billing.stripe.com/p/session/x" });
    expect(await openBillingPortal("tok")).toEqual({
      kind: "ok",
      url: "https://billing.stripe.com/p/session/x",
    });

    respond(404, error("billing_account_not_found"));
    expect(await openBillingPortal("tok")).toEqual({ kind: "not_found" });
  });
});
