/**
 * Typed client for the Tether FastAPI backend.
 *
 * This is the *only* place in the app that reads `NEXT_PUBLIC_API_BASE_URL`.
 * Never call Gemini, Twilio, or any other provider directly from this app —
 * everything goes through FastAPI.
 */

export type HealthApiStatus = "ok" | "degraded";
export type HealthDbStatus = "connected" | "error";

/** The exact JSON contract returned by `GET /health`. */
export interface HealthBody {
  status: HealthApiStatus;
  db: HealthDbStatus;
  version: string;
}

/**
 * Discriminated union covering every outcome the status page needs to
 * render distinctly:
 * - `healthy`: API reachable, body parsed, `status: "ok"` and `db: "connected"`.
 * - `degraded`: API reachable, body parsed, but the DB isn't connected
 *   (HTTP 503 with a parseable body still counts as "degraded", not
 *   "unreachable" — the API answered, it's the DB that's the problem).
 * - `unreachable`: the fetch itself failed, or the response body couldn't
 *   be parsed as the expected shape (wrong base URL, network error, the
 *   API deployment being down, etc).
 */
export type HealthResult =
  | { kind: "healthy"; data: HealthBody }
  | { kind: "degraded"; data: HealthBody }
  | { kind: "unreachable"; reason: string };

function isHealthBody(value: unknown): value is HealthBody {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  return (
    (candidate.status === "ok" || candidate.status === "degraded") &&
    (candidate.db === "connected" || candidate.db === "error") &&
    typeof candidate.version === "string"
  );
}

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
}

/**
 * Calls `GET /health` on the FastAPI backend and normalizes every outcome
 * into a `HealthResult`, so callers never need their own try/catch.
 *
 * Always fetches fresh (`cache: "no-store"`) — this is a liveness check,
 * never cached, and must reflect the current deployment on every request.
 */
export async function getHealth(): Promise<HealthResult> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

  if (!baseUrl) {
    return {
      kind: "unreachable",
      reason: "NEXT_PUBLIC_API_BASE_URL is not configured.",
    };
  }

  let response: Response;
  try {
    response = await fetch(`${normalizeBaseUrl(baseUrl)}/health`, {
      cache: "no-store",
      // Matches the backend's own 5s statement_timeout (core/db.py) — a
      // stalled request should fail closed, not hang the page forever.
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    return {
      kind: "unreachable",
      reason: "Could not reach the API. It may be down or unreachable from here.",
    };
  }

  // The backend's /health contract returns a parseable JSON body on both
  // success (200) and DB failure (503) — so the body is inspected
  // regardless of `response.ok`. Only a thrown fetch or an unparsable /
  // unexpected body counts as "unreachable".
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      kind: "unreachable",
      reason: "The API returned a response that could not be read.",
    };
  }

  if (!isHealthBody(body)) {
    return {
      kind: "unreachable",
      reason: "The API returned an unexpected response shape.",
    };
  }

  if (response.status === 200 && body.status === "ok" && body.db === "connected") {
    return { kind: "healthy", data: body };
  }

  if (response.status === 503 && body.status === "degraded" && body.db === "error") {
    return { kind: "degraded", data: body };
  }

  return {
    kind: "unreachable",
    reason: "The API returned an unexpected response.",
  };
}
