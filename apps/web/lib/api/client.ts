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

/** Billing access for the caller's business (`GET /billing/subscription`, `GET /me`). */
export interface SubscriptionRead {
  entitled: boolean;
  /** Stripe's subscription status, or `null` before any subscription exists. */
  status: string | null;
  trial_ends_at: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  trial_available: boolean;
}

/** `GET /billing/plan`: the price comes from Stripe, in minor units (cents). */
export interface PlanRead {
  amount: number;
  currency: string;
  interval: string;
  trial_days: number;
  trial_available: boolean;
}

export type PlanChoice = "trial" | "paid";

/** The exact JSON contract returned by `GET /me`. */
export interface MeRead {
  id: string;
  email: string;
  business: BusinessRead | null;
  /** `null` until the caller has a business. */
  subscription: SubscriptionRead | null;
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

function isNullableString(value: unknown): boolean {
  return value === null || typeof value === "string";
}

function isSubscriptionRead(value: unknown): value is SubscriptionRead {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  return (
    typeof candidate.entitled === "boolean" &&
    isNullableString(candidate.status) &&
    isNullableString(candidate.trial_ends_at) &&
    isNullableString(candidate.current_period_end) &&
    typeof candidate.cancel_at_period_end === "boolean" &&
    typeof candidate.trial_available === "boolean"
  );
}

function isPlanRead(value: unknown): value is PlanRead {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  return (
    typeof candidate.amount === "number" &&
    typeof candidate.currency === "string" &&
    typeof candidate.interval === "string" &&
    typeof candidate.trial_days === "number" &&
    typeof candidate.trial_available === "boolean"
  );
}

// Checkout and the Billing Portal (test mode uses the same hosts).
const STRIPE_REDIRECT_HOSTS = new Set(["checkout.stripe.com", "billing.stripe.com"]);

/** Only follow redirects to Stripe's own hosted pages, even though they come from our API. */
function isHttpsRedirect(value: unknown): value is { url: string } {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const url = (value as Record<string, unknown>).url;
  if (typeof url !== "string") {
    return false;
  }
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && STRIPE_REDIRECT_HOSTS.has(parsed.hostname);
  } catch {
    return false;
  }
}

function isMeRead(value: unknown): value is MeRead {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  return (
    typeof candidate.id === "string" &&
    typeof candidate.email === "string" &&
    (candidate.business === null || isBusinessRead(candidate.business)) &&
    (candidate.subscription === null || isSubscriptionRead(candidate.subscription))
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
async function fetchApi(
  path: string,
  init: RequestInit,
  timeoutMs = 5000,
): Promise<ApiFetchOutcome> {
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
      signal: AbortSignal.timeout(timeoutMs),
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

// Billing. These calls reach Stripe through the API, so they get a longer
// timeout than plain reads.
const BILLING_TIMEOUT_MS = 20000;

export type PlanResult =
  | { kind: "ok"; data: PlanRead }
  | { kind: "no_business" }
  | { kind: "unauthorized" }
  | { kind: "unreachable"; reason: string };

export type SubscriptionResult =
  | { kind: "ok"; data: SubscriptionRead }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "unreachable"; reason: string };

export type CheckoutResult =
  | { kind: "ok"; url: string }
  | { kind: "subscription_active" }
  | { kind: "trial_already_used" }
  | { kind: "busy" }
  | { kind: "no_business" }
  | { kind: "unauthorized" }
  | { kind: "unreachable"; reason: string };

export type PortalResult =
  | { kind: "ok"; url: string }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "unreachable"; reason: string };

function errorCode(body: unknown): string | null {
  return isApiErrorBody(body) ? body.error.code : null;
}

const UNEXPECTED = { kind: "unreachable", reason: "The API returned an unexpected response." } as const;

/** Calls `GET /api/v1/billing/plan`. */
export async function getBillingPlan(token: string): Promise<PlanResult> {
  const outcome = await fetchApi("/billing/plan", { headers: authHeaders(token) }, BILLING_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 404) return { kind: "no_business" };
  if (outcome.status === 200 && isPlanRead(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `POST /api/v1/billing/checkout`; on success, redirect the browser to `url`. */
export async function startCheckout(token: string, choice: PlanChoice): Promise<CheckoutResult> {
  const outcome = await fetchApi(
    "/billing/checkout",
    { method: "POST", headers: jsonAuthHeaders(token), body: JSON.stringify({ choice }) },
    BILLING_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 404) return { kind: "no_business" };
  if (outcome.status === 409) {
    const code = errorCode(outcome.body);
    if (code === "trial_already_used") return { kind: "trial_already_used" };
    if (code === "billing_busy") return { kind: "busy" };
    return { kind: "subscription_active" };
  }
  if (outcome.status === 200 && isHttpsRedirect(outcome.body)) {
    return { kind: "ok", url: outcome.body.url };
  }
  return UNEXPECTED;
}

/** Calls `POST /api/v1/billing/checkout/{sessionId}/sync` after Stripe redirects back. */
export async function syncCheckout(token: string, sessionId: string): Promise<SubscriptionResult> {
  const outcome = await fetchApi(
    `/billing/checkout/${encodeURIComponent(sessionId)}/sync`,
    { method: "POST", headers: authHeaders(token) },
    BILLING_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  // 422 is a malformed id: from the owner's point of view, the same as unknown.
  if (outcome.status === 404 || outcome.status === 422) return { kind: "not_found" };
  if (outcome.status === 200 && isSubscriptionRead(outcome.body)) {
    return { kind: "ok", data: outcome.body };
  }
  return UNEXPECTED;
}

/** Calls `POST /api/v1/billing/portal`; on success, redirect the browser to `url`. */
export async function openBillingPortal(token: string): Promise<PortalResult> {
  const outcome = await fetchApi(
    "/billing/portal",
    { method: "POST", headers: authHeaders(token) },
    BILLING_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 404) return { kind: "not_found" };
  if (outcome.status === 200 && isHttpsRedirect(outcome.body)) {
    return { kind: "ok", url: outcome.body.url };
  }
  return UNEXPECTED;
}
