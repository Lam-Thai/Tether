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

/** The exact JSON contract returned by `GET /business` / `POST /business` / `PATCH /business/{id}`. */
export interface BusinessRead {
  id: string;
  name: string;
  industry: string;
  address: string;
  timezone: string;
  phone: string | null;
  contact_email: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * The request body shape for `POST /business` and `PATCH /business/{id}`
 * (as a partial, for the latter). `phone` / `contact_email` are optional —
 * omit them (or pass `null`) to leave them unset.
 */
export interface BusinessInput {
  name: string;
  industry: string;
  address: string;
  timezone: string;
  phone?: string | null;
  contact_email?: string | null;
}

/** The exact JSON contract returned by `GET /me`. */
export interface MeRead {
  id: string;
  email: string;
  business: BusinessRead | null;
}

export type MeResult =
  | { kind: "ok"; data: MeRead }
  | { kind: "unauthorized" }
  | { kind: "unreachable"; reason: string };

export type BusinessResult =
  | { kind: "ok"; data: BusinessRead }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "unreachable"; reason: string };

/** `POST /business`: on top of the shared outcomes, a 409 means the tenant already has a business. */
export type CreateBusinessResult =
  | { kind: "ok"; data: BusinessRead }
  | { kind: "already_exists" }
  | { kind: "invalid"; message: string }
  | { kind: "unauthorized" }
  | { kind: "unreachable"; reason: string };

/** `PATCH /business/{id}`: a 404 means the id didn't resolve to the caller's own business. */
export type UpdateBusinessResult =
  | { kind: "ok"; data: BusinessRead }
  | { kind: "not_found" }
  | { kind: "invalid"; message: string }
  | { kind: "unauthorized" }
  | { kind: "unreachable"; reason: string };

function isBusinessRead(value: unknown): value is BusinessRead {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  return (
    typeof candidate.id === "string" &&
    typeof candidate.name === "string" &&
    typeof candidate.industry === "string" &&
    typeof candidate.address === "string" &&
    typeof candidate.timezone === "string" &&
    (typeof candidate.phone === "string" || candidate.phone === null) &&
    (typeof candidate.contact_email === "string" || candidate.contact_email === null) &&
    typeof candidate.created_at === "string" &&
    typeof candidate.updated_at === "string"
  );
}

function isMeRead(value: unknown): value is MeRead {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  return (
    typeof candidate.id === "string" &&
    typeof candidate.email === "string" &&
    (candidate.business === null || isBusinessRead(candidate.business))
  );
}

/** The shared `{"error": {"code": ..., "message": ...}}` envelope every API error uses. */
function isApiErrorBody(value: unknown): value is { error: { code: string; message: string } } {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;
  if (typeof candidate.error !== "object" || candidate.error === null) {
    return false;
  }

  const error = candidate.error as Record<string, unknown>;
  return typeof error.code === "string" && typeof error.message === "string";
}

type ApiFetchOutcome =
  | { kind: "unreachable"; reason: string }
  | { kind: "responded"; status: number; body: unknown };

/**
 * Shared fetch plumbing for every `/api/v1/*` call below: resolves the base
 * URL, performs the request with the same timeout/no-store policy as
 * `getHealth`, and normalizes network failures / unparsable bodies into
 * `{ kind: "unreachable" }` so each endpoint function only has to switch on
 * HTTP status codes it actually expects.
 */
async function fetchApi(path: string, init: RequestInit): Promise<ApiFetchOutcome> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

  if (!baseUrl) {
    return {
      kind: "unreachable",
      reason: "NEXT_PUBLIC_API_BASE_URL is not configured.",
    };
  }

  let response: Response;
  try {
    response = await fetch(`${normalizeBaseUrl(baseUrl)}/api/v1${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
      ...init,
    });
  } catch {
    return {
      kind: "unreachable",
      reason: "Could not reach the API. It may be down or unreachable from here.",
    };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      kind: "unreachable",
      reason: "The API returned a response that could not be read.",
    };
  }

  return { kind: "responded", status: response.status, body };
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

function jsonAuthHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}

function invalidMessage(body: unknown): string {
  return isApiErrorBody(body) ? body.error.message : "The business details are invalid.";
}

/**
 * Calls `GET /api/v1/me`. `token` is a Clerk session token, fetched by the
 * caller (Server Component via `auth()`, Client Component via `useAuth()`)
 * — this module never reads Clerk state itself.
 */
export async function getMe(token: string): Promise<MeResult> {
  const outcome = await fetchApi("/me", { headers: authHeaders(token) });

  if (outcome.kind === "unreachable") {
    return outcome;
  }

  if (outcome.status === 401) {
    return { kind: "unauthorized" };
  }

  if (outcome.status === 200 && isMeRead(outcome.body)) {
    return { kind: "ok", data: outcome.body };
  }

  return { kind: "unreachable", reason: "The API returned an unexpected response." };
}

/** Calls `GET /api/v1/business`. */
export async function getBusiness(token: string): Promise<BusinessResult> {
  const outcome = await fetchApi("/business", { headers: authHeaders(token) });

  if (outcome.kind === "unreachable") {
    return outcome;
  }

  if (outcome.status === 401) {
    return { kind: "unauthorized" };
  }

  if (outcome.status === 404) {
    return { kind: "not_found" };
  }

  if (outcome.status === 200 && isBusinessRead(outcome.body)) {
    return { kind: "ok", data: outcome.body };
  }

  return { kind: "unreachable", reason: "The API returned an unexpected response." };
}

/** Calls `POST /api/v1/business`. */
export async function createBusiness(
  token: string,
  input: BusinessInput,
): Promise<CreateBusinessResult> {
  const outcome = await fetchApi("/business", {
    method: "POST",
    headers: jsonAuthHeaders(token),
    body: JSON.stringify(input),
  });

  if (outcome.kind === "unreachable") {
    return outcome;
  }

  if (outcome.status === 401) {
    return { kind: "unauthorized" };
  }

  if (outcome.status === 409) {
    return { kind: "already_exists" };
  }

  if (outcome.status === 422) {
    return { kind: "invalid", message: invalidMessage(outcome.body) };
  }

  if (outcome.status === 201 && isBusinessRead(outcome.body)) {
    return { kind: "ok", data: outcome.body };
  }

  return { kind: "unreachable", reason: "The API returned an unexpected response." };
}

/** Calls `PATCH /api/v1/business/{businessId}`. */
export async function updateBusiness(
  token: string,
  businessId: string,
  input: Partial<BusinessInput>,
): Promise<UpdateBusinessResult> {
  const outcome = await fetchApi(`/business/${encodeURIComponent(businessId)}`, {
    method: "PATCH",
    headers: jsonAuthHeaders(token),
    body: JSON.stringify(input),
  });

  if (outcome.kind === "unreachable") {
    return outcome;
  }

  if (outcome.status === 401) {
    return { kind: "unauthorized" };
  }

  if (outcome.status === 404) {
    return { kind: "not_found" };
  }

  if (outcome.status === 422) {
    return { kind: "invalid", message: invalidMessage(outcome.body) };
  }

  if (outcome.status === 200 && isBusinessRead(outcome.body)) {
    return { kind: "ok", data: outcome.body };
  }

  return { kind: "unreachable", reason: "The API returned an unexpected response." };
}
