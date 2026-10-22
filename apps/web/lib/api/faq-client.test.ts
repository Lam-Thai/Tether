import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createFaq, deleteFaq, listFaqs, updateFaq, type FaqRead } from "./client";

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_API_BASE_URL;
const ID = "33333333-3333-4333-8333-333333333333";

const FAQ: FaqRead = {
  id: ID,
  question: "When are you open?",
  answer: "9 to 5.",
  category: "Hours",
  active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function respond(status: number, body?: unknown) {
  const response =
    body === undefined ? new Response(null, { status }) : new Response(JSON.stringify(body), { status });
  const fetchMock = vi.fn<(url: string, init: RequestInit & { headers: unknown; method: string; body: string }) => Promise<Response>>().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const error = (code: string, message = code) => ({ error: { code, message } });

describe("faq client", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_ENV;
    vi.unstubAllGlobals();
  });

  describe("listFaqs", () => {
    it("returns the list with the bearer token", async () => {
      const fetchMock = respond(200, { faqs: [FAQ], max_faqs: 50 });
      const result = await listFaqs("tok");

      expect(result).toEqual({ kind: "ok", data: { faqs: [FAQ], max_faqs: 50 } });
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("https://api.example.test/api/v1/faqs");
      expect(init.headers).toEqual({ Authorization: "Bearer tok" });
    });

    it("maps 401 and 402", async () => {
      respond(401, error("unauthorized"));
      expect(await listFaqs("t")).toEqual({ kind: "unauthorized" });
      respond(402, error("subscription_required"));
      expect(await listFaqs("t")).toEqual({ kind: "subscription_required" });
    });

    it("is unreachable on a thrown fetch, malformed JSON or a wrong shape", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await listFaqs("t")).kind).toBe("unreachable");

      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not json", { status: 200 })));
      expect((await listFaqs("t")).kind).toBe("unreachable");

      respond(200, { faqs: [{ id: 1 }], max_faqs: 50 });
      expect((await listFaqs("t")).kind).toBe("unreachable");
    });
  });

  describe("createFaq", () => {
    it("posts the body and returns the created FAQ on 201", async () => {
      const fetchMock = respond(201, FAQ);
      const input = { question: FAQ.question, answer: FAQ.answer, category: "Hours", active: true };
      const result = await createFaq("tok", input);

      expect(result).toEqual({ kind: "ok", data: FAQ });
      const [, init] = fetchMock.mock.calls[0]!;
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual(input);
    });

    it("maps 409 to limit_reached, 422 to validation, 401 and 402", async () => {
      respond(409, error("faq_limit_reached"));
      expect(await createFaq("t", { question: "q", answer: "a" })).toEqual({ kind: "limit_reached" });

      respond(422, error("validation_error", "Bad."));
      expect(await createFaq("t", { question: "q", answer: "a" })).toEqual({
        kind: "validation",
        message: "Bad.",
        fieldErrors: {},
      });

      respond(401, error("unauthorized"));
      expect((await createFaq("t", { question: "q", answer: "a" })).kind).toBe("unauthorized");
      respond(402, error("subscription_required"));
      expect((await createFaq("t", { question: "q", answer: "a" })).kind).toBe("subscription_required");
    });

    it("is unreachable on a thrown fetch or an unexpected status", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await createFaq("t", { question: "q", answer: "a" })).kind).toBe("unreachable");
      respond(500, error("boom"));
      expect((await createFaq("t", { question: "q", answer: "a" })).kind).toBe("unreachable");
    });
  });

  describe("updateFaq", () => {
    it("patches only what it is given", async () => {
      const fetchMock = respond(200, { ...FAQ, active: false });
      const result = await updateFaq("tok", ID, { active: false });

      expect(result).toEqual({ kind: "ok", data: { ...FAQ, active: false } });
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe(`https://api.example.test/api/v1/faqs/${ID}`);
      expect(init.method).toBe("PATCH");
      expect(JSON.parse(init.body)).toEqual({ active: false });
    });

    it("maps 404, 422, 401 and 402", async () => {
      respond(404, error("faq_not_found"));
      expect(await updateFaq("t", ID, {})).toEqual({ kind: "not_found" });
      respond(422, error("validation_error", "Bad."));
      expect((await updateFaq("t", ID, {})).kind).toBe("validation");
      respond(401, error("unauthorized"));
      expect((await updateFaq("t", ID, {})).kind).toBe("unauthorized");
      respond(402, error("subscription_required"));
      expect((await updateFaq("t", ID, {})).kind).toBe("subscription_required");
    });

    it("is unreachable on a thrown fetch or malformed JSON", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await updateFaq("t", ID, {})).kind).toBe("unreachable");
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 200 })));
      expect((await updateFaq("t", ID, {})).kind).toBe("unreachable");
    });
  });

  describe("deleteFaq", () => {
    it("is ok only on 204", async () => {
      const fetchMock = respond(204);
      expect(await deleteFaq("tok", ID)).toEqual({ kind: "ok" });
      expect(fetchMock.mock.calls[0]![1].method).toBe("DELETE");

      respond(200, {});
      expect((await deleteFaq("tok", ID)).kind).toBe("unreachable");
    });

    it("maps 404, 401, 402 and a thrown fetch", async () => {
      respond(404, error("faq_not_found"));
      expect(await deleteFaq("t", ID)).toEqual({ kind: "not_found" });
      respond(401, error("unauthorized"));
      expect((await deleteFaq("t", ID)).kind).toBe("unauthorized");
      respond(402, error("subscription_required"));
      expect((await deleteFaq("t", ID)).kind).toBe("subscription_required");
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
      expect((await deleteFaq("t", ID)).kind).toBe("unreachable");
    });
  });
});
