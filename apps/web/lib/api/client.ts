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
  /** ISO 4217 code; service prices are in this currency. */
  currency: string;
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
  currency?: string;
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
    typeof candidate.currency === "string" &&
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

  // 204 No Content (e.g. DELETE) has no body to parse.
  if (response.status === 204) {
    return { kind: "responded", status: 204, body: null };
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

// Agent persona and the owner's private test chat (`/agent`, `/agent/test`).
// The test chat calls Gemini through the API, so it gets the longest timeout.
const AGENT_TIMEOUT_MS = 10000;
const TURN_TIMEOUT_MS = 45000;

export type AgentTone = "friendly" | "professional" | "concise";

export interface AgentRead {
  id: string;
  name: string;
  tone: AgentTone;
  greeting: string;
  custom_instructions: string;
  active: boolean;
  updated_at: string;
}

/** `PATCH /agent` body: send only the fields that changed. */
export interface AgentUpdate {
  name?: string;
  tone?: AgentTone;
  greeting?: string;
  custom_instructions?: string;
}

export interface MessageRead {
  sequence_number: number;
  speaker: "agent" | "customer";
  text: string;
  created_at: string;
  /** True when this agent message is one of the fixed fallback replies. */
  fallback: boolean;
}

export interface TurnRead {
  interaction_id: string;
  reply_text: string;
  intent: string;
  status: string;
  ended: boolean;
  /** True when the model failed and `reply_text` is one of the fixed fallback messages. */
  fallback: boolean;
  /** True when the fallback is the daily-quota one. Absent on older responses (read as false). */
  usage_limited: boolean;
  /** Messages created by this turn (the greeting first on a new chat). */
  messages: MessageRead[];
}

export interface TranscriptRead {
  interaction_id: string;
  status: string;
  ended: boolean;
  started_at: string;
  ended_at: string | null;
  messages: MessageRead[];
}

export interface TestMessageInput {
  interactionId?: string | null;
  message: string;
}

/** Field name -> message, when the API's 422 envelope carries per-field errors. */
export type FieldErrors = Record<string, string>;

export type AgentResult =
  | { kind: "ok"; data: AgentRead }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "not_found" }
  | { kind: "unreachable"; reason: string };

export type UpdateAgentResult =
  | { kind: "ok"; data: AgentRead }
  | { kind: "validation"; message: string; fieldErrors: FieldErrors }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "not_found" }
  | { kind: "unreachable"; reason: string };

export type TestTurnResult =
  | { kind: "ok"; data: TurnRead }
  | { kind: "validation"; message: string; fieldErrors: FieldErrors }
  | { kind: "ended" }
  /** 409 `interaction_busy`: another send or a lock timeout; the chat is still open. */
  | { kind: "busy" }
  /** 429 `concurrency_limited`: too many active chats to start a new one. */
  | { kind: "concurrency_limited" }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

export type TranscriptResult =
  | { kind: "ok"; data: TranscriptRead }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isAgentRead(value: unknown): value is AgentRead {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    (value.tone === "friendly" || value.tone === "professional" || value.tone === "concise") &&
    typeof value.greeting === "string" &&
    typeof value.custom_instructions === "string" &&
    typeof value.active === "boolean" &&
    typeof value.updated_at === "string"
  );
}

function isMessageRead(value: unknown): value is MessageRead {
  if (!isRecord(value)) return false;
  return (
    typeof value.sequence_number === "number" &&
    (value.speaker === "agent" || value.speaker === "customer") &&
    typeof value.text === "string" &&
    typeof value.created_at === "string" &&
    // Older responses have no flag; it is normalized to false below.
    (value.fallback === undefined || typeof value.fallback === "boolean")
  );
}

function withFallbackFlags(messages: MessageRead[]): MessageRead[] {
  return messages.map((m) => ({ ...m, fallback: m.fallback === true }));
}

function isMessageList(value: unknown): value is MessageRead[] {
  return Array.isArray(value) && value.every(isMessageRead);
}

function isTurnRead(value: unknown): value is TurnRead {
  if (!isRecord(value)) return false;
  return (
    typeof value.interaction_id === "string" &&
    typeof value.reply_text === "string" &&
    typeof value.intent === "string" &&
    typeof value.status === "string" &&
    typeof value.ended === "boolean" &&
    typeof value.fallback === "boolean" &&
    (value.usage_limited === undefined || typeof value.usage_limited === "boolean") &&
    isMessageList(value.messages)
  );
}

function isTranscriptRead(value: unknown): value is TranscriptRead {
  if (!isRecord(value)) return false;
  return (
    typeof value.interaction_id === "string" &&
    typeof value.status === "string" &&
    typeof value.ended === "boolean" &&
    typeof value.started_at === "string" &&
    isNullableString(value.ended_at) &&
    isMessageList(value.messages)
  );
}

/**
 * The contract's 422 envelope is `{error: {code, message}}`. If the API also
 * adds `error.fields` (field -> message), surface it; otherwise only the
 * message is available.
 */
function validationFrom(body: unknown, fallbackMessage: string) {
  const message = isApiErrorBody(body) ? body.error.message : fallbackMessage;
  const fieldErrors: FieldErrors = {};
  if (isRecord(body) && isRecord(body.error) && isRecord(body.error.fields)) {
    for (const [field, value] of Object.entries(body.error.fields)) {
      if (typeof value === "string") fieldErrors[field] = value;
    }
  }
  return { message, fieldErrors };
}

/** Calls `GET /api/v1/agent` (the API creates the default agent on first read). */
export async function getAgent(token: string): Promise<AgentResult> {
  const outcome = await fetchApi("/agent", { headers: authHeaders(token) }, AGENT_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404) return { kind: "not_found" };
  if (outcome.status === 200 && isAgentRead(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `PATCH /api/v1/agent`. */
export async function updateAgent(token: string, patch: AgentUpdate): Promise<UpdateAgentResult> {
  const outcome = await fetchApi(
    "/agent",
    { method: "PATCH", headers: jsonAuthHeaders(token), body: JSON.stringify(patch) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404) return { kind: "not_found" };
  if (outcome.status === 422) {
    return { kind: "validation", ...validationFrom(outcome.body, "The agent details are invalid.") };
  }
  if (outcome.status === 200 && isAgentRead(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/**
 * Calls `POST /api/v1/agent/test`. Omit `interactionId` to start a new chat;
 * the API persists the greeting and returns it in `messages`.
 */
export async function sendTestMessage(
  token: string,
  input: TestMessageInput,
): Promise<TestTurnResult> {
  const body: { message: string; interaction_id?: string } = { message: input.message };
  if (input.interactionId) body.interaction_id = input.interactionId;

  const outcome = await fetchApi(
    "/agent/test",
    { method: "POST", headers: jsonAuthHeaders(token), body: JSON.stringify(body) },
    TURN_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404) return { kind: "not_found" };
  if (outcome.status === 409) {
    return errorCode(outcome.body) === "interaction_busy" ? { kind: "busy" } : { kind: "ended" };
  }
  if (outcome.status === 429) {
    return errorCode(outcome.body) === "concurrency_limited" ? { kind: "concurrency_limited" } : UNEXPECTED;
  }
  if (outcome.status === 422) {
    return { kind: "validation", ...validationFrom(outcome.body, "That message cannot be sent.") };
  }
  if (outcome.status === 200 && isTurnRead(outcome.body)) {
    return {
      kind: "ok",
      data: {
        ...outcome.body,
        usage_limited: outcome.body.usage_limited === true,
        messages: withFallbackFlags(outcome.body.messages),
      },
    };
  }
  return UNEXPECTED;
}

/** Calls `GET /api/v1/agent/test/{id}`. A malformed id (422) is treated as unknown. */
export async function getTestTranscript(token: string, id: string): Promise<TranscriptResult> {
  const outcome = await fetchApi(
    `/agent/test/${encodeURIComponent(id)}`,
    { headers: authHeaders(token) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404 || outcome.status === 422) return { kind: "not_found" };
  if (outcome.status === 200 && isTranscriptRead(outcome.body)) {
    return { kind: "ok", data: { ...outcome.body, messages: withFallbackFlags(outcome.body.messages) } };
  }
  return UNEXPECTED;
}

/** Calls `POST /api/v1/agent/test/{id}/end` (idempotent). */
export async function endTestChat(token: string, id: string): Promise<TranscriptResult> {
  const outcome = await fetchApi(
    `/agent/test/${encodeURIComponent(id)}/end`,
    { method: "POST", headers: authHeaders(token) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404 || outcome.status === 422) return { kind: "not_found" };
  if (outcome.status === 200 && isTranscriptRead(outcome.body)) {
    return { kind: "ok", data: { ...outcome.body, messages: withFallbackFlags(outcome.body.messages) } };
  }
  return UNEXPECTED;
}

// Usage limits (`GET /usage`).

/** Meter kinds the UI has a label for; the API may send others. */
export type UsageMeterKind = "gemini_request" | "tts_request" | "call" | "sms" | (string & {});

export interface UsageMeter {
  kind: UsageMeterKind;
  used: number;
  limit: number;
  /** The platform-wide daily cap is exhausted, even if this business has room. */
  global_limit_reached: boolean;
}

export interface UsageRead {
  window: { timezone: string; starts_at: string; resets_at: string };
  meters: UsageMeter[];
  gemini_tokens: number;
  failed_requests: number;
  /** Decimal string in USD. An estimate, never a billed amount. */
  estimated_cost_usd: string;
  concurrency: { active: number; limit: number; idle_minutes: number; global_limit_reached: boolean };
  max_call_duration_seconds: number;
}

export type UsageResult =
  | { kind: "ok"; data: UsageRead }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isUsageMeter(value: unknown): value is UsageMeter {
  if (!isRecord(value)) return false;
  return (
    typeof value.kind === "string" &&
    isCount(value.used) &&
    isCount(value.limit) &&
    typeof value.global_limit_reached === "boolean"
  );
}

function isUsageRead(value: unknown): value is UsageRead {
  if (!isRecord(value)) return false;
  const w = value.window;
  const c = value.concurrency;
  return (
    isRecord(w) &&
    typeof w.timezone === "string" &&
    typeof w.starts_at === "string" &&
    typeof w.resets_at === "string" &&
    Array.isArray(value.meters) &&
    value.meters.every(isUsageMeter) &&
    isCount(value.gemini_tokens) &&
    isCount(value.failed_requests) &&
    typeof value.estimated_cost_usd === "string" &&
    Number.isFinite(Number(value.estimated_cost_usd)) &&
    isRecord(c) &&
    isCount(c.active) &&
    isCount(c.limit) &&
    isCount(c.idle_minutes) &&
    typeof c.global_limit_reached === "boolean" &&
    isCount(value.max_call_duration_seconds)
  );
}

/** Calls `GET /api/v1/usage`: today's metered usage against the business's limits. */
export async function getUsage(token: string): Promise<UsageResult> {
  const outcome = await fetchApi("/usage", { headers: authHeaders(token) }, AGENT_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 200 && isUsageRead(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

// FAQs (`/faqs`): the owner's approved answers the receptionist may use.

export interface FaqRead {
  id: string;
  question: string;
  answer: string;
  category: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface FaqList {
  faqs: FaqRead[];
  max_faqs: number;
}

/** `POST /faqs` body. A blank or null category means none. */
export interface FaqCreate {
  question: string;
  answer: string;
  category?: string | null;
  active?: boolean;
}

/** `PATCH /faqs/{id}` body: send only the fields that changed. */
export type FaqUpdate = Partial<FaqCreate>;

export type FaqListResult =
  | { kind: "ok"; data: FaqList }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

export type CreateFaqResult =
  | { kind: "ok"; data: FaqRead }
  | { kind: "validation"; message: string; fieldErrors: FieldErrors }
  /** 409 `faq_limit_reached`: the business is at its FAQ cap. */
  | { kind: "limit_reached" }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

export type UpdateFaqResult =
  | { kind: "ok"; data: FaqRead }
  | { kind: "validation"; message: string; fieldErrors: FieldErrors }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

export type DeleteFaqResult =
  | { kind: "ok" }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

function isFaqRead(value: unknown): value is FaqRead {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.question === "string" &&
    typeof value.answer === "string" &&
    isNullableString(value.category) &&
    typeof value.active === "boolean" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

function isFaqList(value: unknown): value is FaqList {
  if (!isRecord(value)) return false;
  return Array.isArray(value.faqs) && value.faqs.every(isFaqRead) && isCount(value.max_faqs);
}

/** Calls `GET /api/v1/faqs`. */
export async function listFaqs(token: string): Promise<FaqListResult> {
  const outcome = await fetchApi("/faqs", { headers: authHeaders(token) }, AGENT_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 200 && isFaqList(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `POST /api/v1/faqs`. */
export async function createFaq(token: string, input: FaqCreate): Promise<CreateFaqResult> {
  const outcome = await fetchApi(
    "/faqs",
    { method: "POST", headers: jsonAuthHeaders(token), body: JSON.stringify(input) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 409) return { kind: "limit_reached" };
  if (outcome.status === 422) {
    return { kind: "validation", ...validationFrom(outcome.body, "The FAQ details are invalid.") };
  }
  if (outcome.status === 201 && isFaqRead(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `PATCH /api/v1/faqs/{id}`. */
export async function updateFaq(token: string, id: string, patch: FaqUpdate): Promise<UpdateFaqResult> {
  const outcome = await fetchApi(
    `/faqs/${encodeURIComponent(id)}`,
    { method: "PATCH", headers: jsonAuthHeaders(token), body: JSON.stringify(patch) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404) return { kind: "not_found" };
  if (outcome.status === 422) {
    return { kind: "validation", ...validationFrom(outcome.body, "The FAQ details are invalid.") };
  }
  if (outcome.status === 200 && isFaqRead(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `DELETE /api/v1/faqs/{id}`; only a 204 counts as deleted. */
export async function deleteFaq(token: string, id: string): Promise<DeleteFaqResult> {
  const outcome = await fetchApi(
    `/faqs/${encodeURIComponent(id)}`,
    { method: "DELETE", headers: authHeaders(token) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404) return { kind: "not_found" };
  if (outcome.status === 204) return { kind: "ok" };
  return UNEXPECTED;
}

// Services (`/services`): what the business sells, with duration and an optional listed price.

export interface ServiceRead {
  id: string;
  name: string;
  description: string | null;
  duration_minutes: number;
  /** Decimal string such as "45.00", or null when there is no listed price. */
  price: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ServiceList {
  services: ServiceRead[];
  currency: string;
  max_services: number;
}

/** `POST /services` body. A null description / null price means none. */
export interface ServiceCreate {
  name: string;
  description?: string | null;
  duration_minutes: number;
  price?: string | null;
  active?: boolean;
}

/** `PATCH /services/{id}` body: send only the fields that changed. */
export type ServiceUpdate = Partial<ServiceCreate>;

export type ServiceListResult =
  | { kind: "ok"; data: ServiceList }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

export type CreateServiceResult =
  | { kind: "ok"; data: ServiceRead }
  | { kind: "validation"; message: string; fieldErrors: FieldErrors }
  /** 409 `service_limit_reached`. */
  | { kind: "limit_reached" }
  /** 409 `service_name_taken`: names are unique per business, ignoring case. */
  | { kind: "name_taken" }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

export type UpdateServiceResult =
  | { kind: "ok"; data: ServiceRead }
  | { kind: "validation"; message: string; fieldErrors: FieldErrors }
  | { kind: "name_taken" }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

export type DeleteServiceResult =
  | { kind: "ok" }
  | { kind: "not_found" }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

function isServiceRead(value: unknown): value is ServiceRead {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    isNullableString(value.description) &&
    isCount(value.duration_minutes) &&
    isNullableString(value.price) &&
    typeof value.active === "boolean" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

function isServiceList(value: unknown): value is ServiceList {
  if (!isRecord(value)) return false;
  return (
    Array.isArray(value.services) &&
    value.services.every(isServiceRead) &&
    typeof value.currency === "string" &&
    isCount(value.max_services)
  );
}

/** Calls `GET /api/v1/services` (inactive services included). */
export async function listServices(token: string): Promise<ServiceListResult> {
  const outcome = await fetchApi("/services", { headers: authHeaders(token) }, AGENT_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 200 && isServiceList(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `POST /api/v1/services`. */
export async function createService(token: string, input: ServiceCreate): Promise<CreateServiceResult> {
  const outcome = await fetchApi(
    "/services",
    { method: "POST", headers: jsonAuthHeaders(token), body: JSON.stringify(input) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 409) {
    const code = errorCode(outcome.body);
    if (code === "service_limit_reached") return { kind: "limit_reached" };
    if (code === "service_name_taken") return { kind: "name_taken" };
    return UNEXPECTED;
  }
  if (outcome.status === 422) {
    return { kind: "validation", ...validationFrom(outcome.body, "The service details are invalid.") };
  }
  if (outcome.status === 201 && isServiceRead(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `PATCH /api/v1/services/{id}`. */
export async function updateService(
  token: string,
  id: string,
  patch: ServiceUpdate,
): Promise<UpdateServiceResult> {
  const outcome = await fetchApi(
    `/services/${encodeURIComponent(id)}`,
    { method: "PATCH", headers: jsonAuthHeaders(token), body: JSON.stringify(patch) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404) return { kind: "not_found" };
  if (outcome.status === 409) {
    return errorCode(outcome.body) === "service_name_taken" ? { kind: "name_taken" } : UNEXPECTED;
  }
  if (outcome.status === 422) {
    return { kind: "validation", ...validationFrom(outcome.body, "The service details are invalid.") };
  }
  if (outcome.status === 200 && isServiceRead(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `DELETE /api/v1/services/{id}`; only a 204 counts as deleted. */
export async function deleteService(token: string, id: string): Promise<DeleteServiceResult> {
  const outcome = await fetchApi(
    `/services/${encodeURIComponent(id)}`,
    { method: "DELETE", headers: authHeaders(token) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 404) return { kind: "not_found" };
  if (outcome.status === 204) return { kind: "ok" };
  return UNEXPECTED;
}

// Business hours (`/business/hours`): one row per weekday, Monday = 0.

export interface DayHours {
  day_of_week: number;
  is_closed: boolean;
  /** "HH:MM:SS", null when closed. */
  opens_at: string | null;
  closes_at: string | null;
}

export interface BusinessHours {
  timezone: string;
  /** False until the owner first saves hours; then `days` is empty. Not the same as closed. */
  configured: boolean;
  days: DayHours[];
}

export type BusinessHoursResult =
  | { kind: "ok"; data: BusinessHours }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

export type PutBusinessHoursResult =
  | { kind: "ok"; data: BusinessHours }
  | { kind: "validation"; message: string; fieldErrors: FieldErrors }
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  | { kind: "unreachable"; reason: string };

function isDayHours(value: unknown): value is DayHours {
  if (!isRecord(value)) return false;
  return (
    typeof value.day_of_week === "number" &&
    Number.isInteger(value.day_of_week) &&
    value.day_of_week >= 0 &&
    value.day_of_week <= 6 &&
    typeof value.is_closed === "boolean" &&
    isNullableString(value.opens_at) &&
    isNullableString(value.closes_at)
  );
}

function isBusinessHours(value: unknown): value is BusinessHours {
  if (!isRecord(value)) return false;
  return (
    typeof value.timezone === "string" &&
    typeof value.configured === "boolean" &&
    Array.isArray(value.days) &&
    value.days.every(isDayHours)
  );
}

/** Calls `GET /api/v1/business/hours`. */
export async function getBusinessHours(token: string): Promise<BusinessHoursResult> {
  const outcome = await fetchApi("/business/hours", { headers: authHeaders(token) }, AGENT_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 200 && isBusinessHours(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `PUT /api/v1/business/hours`: replaces the whole week (exactly 7 days). */
export async function putBusinessHours(token: string, days: DayHours[]): Promise<PutBusinessHoursResult> {
  const outcome = await fetchApi(
    "/business/hours",
    { method: "PUT", headers: jsonAuthHeaders(token), body: JSON.stringify({ days }) },
    AGENT_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 422) {
    return { kind: "validation", ...validationFrom(outcome.body, "The business hours are invalid.") };
  }
  if (outcome.status === 200 && isBusinessHours(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

// Google Calendar connection (`/calendar`). The API owns every token: this
// app only ever sees the Google authorization URL and the status payload.

export type CalendarConnectionStatus =
  | "not_connected"
  | "needs_selection"
  | "connected"
  | "reconnect_needed";

export interface CalendarStatus {
  status: CalendarConnectionStatus;
  calendar_id: string | null;
  calendar_name: string | null;
  /** A fixed reason code (`auth_revoked`, `unavailable`, `calendar_missing`, ...), never provider text. */
  last_error: string | null;
  last_synced_at: string | null;
}

export interface CalendarOption {
  id: string;
  name: string;
  primary: boolean;
  time_zone: string | null;
}

/** Failures every calendar call can end in, besides its own. */
export type CalendarCommonFailure =
  | { kind: "unauthorized" }
  | { kind: "subscription_required" }
  /** 503 `calendar_not_configured`: this deployment has no Google credentials. */
  | { kind: "not_configured" }
  /** 503 `calendar_unavailable`: Google could not be reached. */
  | { kind: "unavailable" }
  | { kind: "unreachable"; reason: string };

export type CalendarStatusResult = { kind: "ok"; data: CalendarStatus } | CalendarCommonFailure;

export type CalendarConnectResult =
  | { kind: "ok"; url: string }
  /** The API sent a URL that is not an `https://accounts.google.com` one; it is never followed. */
  | { kind: "invalid_url" }
  | CalendarCommonFailure;

export type CompleteCalendarResult =
  | { kind: "ok"; data: CalendarStatus }
  | { kind: "invalid_state" }
  | { kind: "scopes_missing" }
  | { kind: "grant_incomplete" }
  | CalendarCommonFailure;

export type ListCalendarsResult =
  | { kind: "ok"; calendars: CalendarOption[] }
  | { kind: "not_connected" }
  | { kind: "reconnect_needed" }
  | CalendarCommonFailure;

export type SelectCalendarResult =
  | { kind: "ok"; data: CalendarStatus }
  | { kind: "calendar_not_found" }
  | { kind: "not_connected" }
  | { kind: "reconnect_needed" }
  | CalendarCommonFailure;

export type DisconnectCalendarResult =
  /** `revoked: false` means the connection is gone here but Google did not confirm the revoke. */
  | { kind: "ok"; revoked: boolean }
  | { kind: "not_connected" }
  | CalendarCommonFailure;

const CALENDAR_STATUSES: readonly string[] = [
  "not_connected",
  "needs_selection",
  "connected",
  "reconnect_needed",
];

const GOOGLE_AUTH_HOST = "accounts.google.com";

function isCalendarStatus(value: unknown): value is CalendarStatus {
  if (!isRecord(value)) return false;
  return (
    typeof value.status === "string" &&
    CALENDAR_STATUSES.includes(value.status) &&
    isNullableString(value.calendar_id) &&
    isNullableString(value.calendar_name) &&
    isNullableString(value.last_error) &&
    isNullableString(value.last_synced_at)
  );
}

function isCalendarOption(value: unknown): value is CalendarOption {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.primary === "boolean" &&
    isNullableString(value.time_zone)
  );
}

/** Only follow a redirect to Google's own consent screen, even though it comes from our API. */
function isGoogleAuthUrl(value: unknown): value is { url: string } {
  if (!isRecord(value) || typeof value.url !== "string") return false;
  try {
    const parsed = new URL(value.url);
    return parsed.protocol === "https:" && parsed.hostname === GOOGLE_AUTH_HOST;
  } catch {
    return false;
  }
}

/** The failures that look the same on every calendar endpoint; null when the response is something else. */
function calendarCommonFailure(outcome: {
  status: number;
  body: unknown;
}): Exclude<CalendarCommonFailure, { kind: "unreachable" }> | null {
  if (outcome.status === 401) return { kind: "unauthorized" };
  if (outcome.status === 402) return { kind: "subscription_required" };
  if (outcome.status === 503) {
    const code = errorCode(outcome.body);
    if (code === "calendar_not_configured") return { kind: "not_configured" };
    if (code === "calendar_unavailable") return { kind: "unavailable" };
  }
  return null;
}

/** Calls `GET /api/v1/calendar/status`. */
export async function getCalendarStatus(token: string): Promise<CalendarStatusResult> {
  const outcome = await fetchApi("/calendar/status", { headers: authHeaders(token) }, BILLING_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  const common = calendarCommonFailure(outcome);
  if (common) return common;
  if (outcome.status === 200 && isCalendarStatus(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `GET /api/v1/calendar/connect`; on success, send the browser to `url`. */
export async function getCalendarConnectUrl(token: string): Promise<CalendarConnectResult> {
  const outcome = await fetchApi("/calendar/connect", { headers: authHeaders(token) }, BILLING_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  const common = calendarCommonFailure(outcome);
  if (common) return common;
  if (outcome.status === 200) {
    return isGoogleAuthUrl(outcome.body) ? { kind: "ok", url: outcome.body.url } : { kind: "invalid_url" };
  }
  return UNEXPECTED;
}

/**
 * Calls `POST /api/v1/calendar/callback` with the `code` and `state` Google
 * put in the redirect. A code is single-use, so callers must send it once.
 */
export async function completeCalendarConnection(
  token: string,
  code: string,
  state: string,
): Promise<CompleteCalendarResult> {
  const outcome = await fetchApi(
    "/calendar/callback",
    { method: "POST", headers: jsonAuthHeaders(token), body: JSON.stringify({ code, state }) },
    BILLING_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  const common = calendarCommonFailure(outcome);
  if (common) return common;
  if (outcome.status === 400) {
    const reason = errorCode(outcome.body);
    if (reason === "invalid_oauth_state") return { kind: "invalid_state" };
    if (reason === "calendar_scopes_missing") return { kind: "scopes_missing" };
    if (reason === "calendar_grant_incomplete") return { kind: "grant_incomplete" };
    return UNEXPECTED;
  }
  if (outcome.status === 200 && isCalendarStatus(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `GET /api/v1/calendar/calendars`: the calendars the owner may pick from. */
export async function listCalendars(token: string): Promise<ListCalendarsResult> {
  const outcome = await fetchApi("/calendar/calendars", { headers: authHeaders(token) }, BILLING_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  const common = calendarCommonFailure(outcome);
  if (common) return common;
  if (outcome.status === 404 && errorCode(outcome.body) === "calendar_not_connected") {
    return { kind: "not_connected" };
  }
  if (outcome.status === 409 && errorCode(outcome.body) === "calendar_reconnect_needed") {
    return { kind: "reconnect_needed" };
  }
  if (
    outcome.status === 200 &&
    isRecord(outcome.body) &&
    Array.isArray(outcome.body.calendars) &&
    outcome.body.calendars.every(isCalendarOption)
  ) {
    return { kind: "ok", calendars: outcome.body.calendars };
  }
  return UNEXPECTED;
}

/** Calls `PATCH /api/v1/calendar`: choose which calendar bookings go to. */
export async function selectCalendar(token: string, calendarId: string): Promise<SelectCalendarResult> {
  const outcome = await fetchApi(
    "/calendar",
    { method: "PATCH", headers: jsonAuthHeaders(token), body: JSON.stringify({ calendar_id: calendarId }) },
    BILLING_TIMEOUT_MS,
  );

  if (outcome.kind === "unreachable") return outcome;
  const common = calendarCommonFailure(outcome);
  if (common) return common;
  if (outcome.status === 404) {
    return errorCode(outcome.body) === "calendar_not_connected"
      ? { kind: "not_connected" }
      : { kind: "calendar_not_found" };
  }
  if (outcome.status === 409 && errorCode(outcome.body) === "calendar_reconnect_needed") {
    return { kind: "reconnect_needed" };
  }
  if (outcome.status === 422) return { kind: "calendar_not_found" };
  if (outcome.status === 200 && isCalendarStatus(outcome.body)) return { kind: "ok", data: outcome.body };
  return UNEXPECTED;
}

/** Calls `DELETE /api/v1/calendar`; the stored connection is gone on any 200. */
export async function disconnectCalendar(token: string): Promise<DisconnectCalendarResult> {
  const outcome = await fetchApi("/calendar", { method: "DELETE", headers: authHeaders(token) }, BILLING_TIMEOUT_MS);

  if (outcome.kind === "unreachable") return outcome;
  const common = calendarCommonFailure(outcome);
  if (common) return common;
  if (outcome.status === 404 && errorCode(outcome.body) === "calendar_not_connected") {
    return { kind: "not_connected" };
  }
  if (outcome.status === 200 && isRecord(outcome.body) && typeof outcome.body.revoked === "boolean") {
    return { kind: "ok", revoked: outcome.body.revoked };
  }
  return UNEXPECTED;
}
