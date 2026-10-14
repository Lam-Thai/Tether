import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getHealth } from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;

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

  it("returns unreachable when NEXT_PUBLIC_API_BASE_URL is not configured", async () => {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const result = await getHealth();

    expect(result.kind).toBe("unreachable");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
