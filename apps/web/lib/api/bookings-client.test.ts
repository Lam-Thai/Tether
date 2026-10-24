import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { listChatBookings, type BookingRead } from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;
const ID = "22222222-2222-4222-8222-222222222222";

const BOOKING: BookingRead = {
  id: "b1",
  service_name: "Haircut",
  start: "2026-10-09T15:30:00-04:00",
  end: "2026-10-09T16:15:00-04:00",
  date: "2026-10-09",
  label: "15:30",
  customer_name: "Alex",
  status: "confirmed",
};

function respond(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("listChatBookings", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  it("returns the bookings with a bearer token and the interaction id", async () => {
    const fetchMock = respond(200, { bookings: [BOOKING] });
    expect(await listChatBookings("tok", ID)).toEqual({ kind: "ok", data: [BOOKING] });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`https://api.example.test/api/v1/bookings?interaction_id=${ID}`);
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("returns an empty list", async () => {
    respond(200, { bookings: [] });
    expect(await listChatBookings("tok", ID)).toEqual({ kind: "ok", data: [] });
  });

  it("maps 401 and 402", async () => {
    respond(401, { error: { code: "unauthorized", message: "x" } });
    expect(await listChatBookings("tok", ID)).toEqual({ kind: "unauthorized" });
    respond(402, { error: { code: "subscription_required", message: "x" } });
    expect(await listChatBookings("tok", ID)).toEqual({ kind: "subscription_required" });
  });

  it("is unreachable on an error body, 422, or an unknown status value", async () => {
    respond(500, { error: { code: "internal", message: "boom" } });
    expect((await listChatBookings("tok", ID)).kind).toBe("unreachable");
    respond(422, { error: { code: "validation_error", message: "x" } });
    expect((await listChatBookings("tok", ID)).kind).toBe("unreachable");
    respond(200, { bookings: [{ ...BOOKING, status: "booked" }] });
    expect((await listChatBookings("tok", ID)).kind).toBe("unreachable");
    respond(200, { nope: true });
    expect((await listChatBookings("tok", ID)).kind).toBe("unreachable");
  });

  it("is unreachable when fetch throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    expect((await listChatBookings("tok", ID)).kind).toBe("unreachable");
  });

  it("is unreachable on malformed JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json", { status: 200 })));
    expect((await listChatBookings("tok", ID)).kind).toBe("unreachable");
  });
});
