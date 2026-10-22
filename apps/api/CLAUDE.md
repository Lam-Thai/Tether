# apps/api

FastAPI backend. See the root `CLAUDE.md` for the hard rules and the layer map (`api / core / schemas / services / repositories / models / integrations / tests`) — this file only adds conventions specific to this app.

## Layout

```
main.py              # entrypoint: `from core.app import create_app; app = create_app()`
core/                 # settings, db engine/session, app factory, error envelope + handlers
schemas/              # Pydantic request/response models
services/             # business logic (deep modules) — no FastAPI or SQLAlchemy imports
repositories/         # the only layer that queries the DB
models/               # SQLAlchemy ORM models + shared declarative Base
integrations/         # external provider clients, each behind a port + in-memory fake (clerk/, stripe/, llm/)
api/                  # routers; `api/health.py` mounted at app root, `api/v1/` for everything else
migrations/           # async Alembic environment
tests/                # mirrors the layers above
```

## Settings

`core.config.Settings` is the *only* place that reads environment variables. Every other module imports the `settings` singleton from `core.config` instead of touching `os.environ`. Required: `DATABASE_URL`, `CLERK_ISSUER` (e.g. `https://<slug>.clerk.accounts.dev`; `clerk_jwks_url` is derived from it, never set separately). Required for billing: `STRIPE_SECRET_KEY` (test-mode `sk_test_`/`rk_test_` unless `ENVIRONMENT=production`; a live key elsewhere fails at startup), `STRIPE_WEBHOOK_SECRET` (`whsec_`, from `stripe listen` locally or the dashboard endpoint in deploys), `STRIPE_PRICE_ID`, `APP_BASE_URL` (the web origin Checkout and the Portal return to). Gemini: `GEMINI_API_KEY` (required when `ENVIRONMENT=production`, optional elsewhere so CI runs on the fake), `GEMINI_MODEL` (default `gemini-2.5-flash`; never hardcode a model id anywhere else), `GEMINI_TIMEOUT_SECONDS` (per attempt, default 10), `CONVERSATION_HISTORY_LIMIT` (messages sent per turn, default 20), `CONVERSATION_TURN_TIMEOUT_SECONDS` (ceiling on all model work in one turn, default 25). Usage limits (all optional, see "Usage limits" below): `GEMINI_REQUESTS_PER_DAY` / `GEMINI_REQUESTS_PER_BUSINESS_PER_DAY` (200 / 50), `TTS_REQUESTS_PER_DAY` / `..._PER_BUSINESS_PER_DAY` (50 / 10), `CALLS_PER_DAY` / `CALLS_PER_BUSINESS_PER_DAY` (50 / 10), `SMS_PER_DAY` / `SMS_PER_BUSINESS_PER_DAY` (50 / 10), `MAX_CALL_DURATION_SECONDS` (600), `MAX_CONCURRENT_INTERACTIONS` / `..._PER_BUSINESS` (20 / 5), `INTERACTION_IDLE_MINUTES` (15), `GEMINI_INPUT_USD_PER_MILLION_TOKENS` / `GEMINI_OUTPUT_USD_PER_MILLION_TOKENS` (0.30 / 2.50, estimated cost only). FAQs: `FAQ_MAX_PER_BUSINESS` (200; creating more is 409 `faq_limit_reached`). Optional: `BILLING_TRIAL_DAYS` (default 14), `CORS_ORIGINS` (JSON array or comma-separated string), `CLERK_AUTHORIZED_PARTIES` (JSON array or comma-separated string of allowed `azp` origins — empty skips the check, for local dev), `ENVIRONMENT`, `APP_VERSION`. The app never reads `CLERK_SECRET_KEY`: RS256 verification only needs Clerk's public JWKS, so the backend has no Clerk secret at all.

## Auth and tenancy

- `core/auth.py`: `verify_principal(token, resolve_signing_key, issuer, authorized_parties) -> Principal` is the only function that knows a Clerk token's claim shape (`sub`, `email`, `azp`). Every failure — bad signature, expired, wrong issuer, unrecognized `azp`, unreachable JWKS — maps to the same `AuthError` (401). `resolve_signing_key` is the `integrations/clerk` port; tests pass an in-memory fake backed by a real RSA keypair instead of a live JWKS fetch.
- `core/tenant.py`: `get_tenant` is the `TenantContext` FastAPI dependency — `get_tenant() -> Tenant(user, business)`. It verifies the bearer token, just-in-time upserts the `users` row from the Clerk subject, and resolves `business` from `owner_id` only. `business` is `None` before onboarding. Every business-scoped router depends on `Tenant`, never on a raw `business_id` from the client.
- `repositories/base.py`: the `BusinessScopedRepository` pattern from issue #3, implemented as a function (`business_scoped_select(model, business_id)`) rather than a class, to match this repo's function-style repositories (see `repositories/health.py`). Every domain repository with a `business_id` column builds its queries on this, always called with `tenant.business.id` — never a client-supplied id.
- Services stay free of SQLAlchemy imports by taking repository functions pre-bound to the session via `functools.partial` (see `services/business.py` / `api/v1/business.py`), the same pattern `services/health.py` already uses for `check_db`.

## DB sessions

`core.db` builds one async SQLAlchemy engine per process (`NullPool`, `pool_pre_ping=True`, 5s statement timeout — serverless-safe, since Neon's own pooler handles connection pooling at the proxy layer) and exposes `get_session()` as a FastAPI dependency that yields one session per request. A plain `postgresql://` URL (what Neon's UI gives you) is rewritten to `postgresql+asyncpg://` automatically.

## Error model

Every error response is the same envelope: `{"error": {"code": "<snake_case_code>", "message": "<human message>"}}`. `core/errors.py` defines `AppError` (status_code + code class attrs) as the base for domain errors, and registers handlers for `AppError`, Starlette's `HTTPException`, `RequestValidationError` (422, `validation_error`), and a catch-all (500, `internal_error` — the real exception is logged server-side via `logging`, never leaked to the client). Add new domain error types by subclassing `AppError`; don't invent a new envelope shape.

## Repository pattern

`repositories/` is the only layer that queries the DB. The health check (`repositories/health.py`) is a connectivity probe with no tenant data, so it doesn't take a `business_id`. `repositories/users.py` and `repositories/business.py` are the two exceptions that establish identity before a `Tenant` exists (scoped by `external_auth_id` / `owner_id`); every later domain table with its own `business_id` column scopes through `repositories/base.py` instead (see "Auth and tenancy" above).

## Routing convention

Liveness/infra checks (`/health`) mount unversioned at the app root — they're not business endpoints. Everything else mounts under `/api/v1` (see `api/v1/router.py`, currently empty). Routers only parse the request, call one service method, and shape the response — no business logic or direct DB access in `api/`.

## Alembic

Async environment (`migrations/env.py`) reads `settings.database_url` through the same normalization `core.db` uses — never a hardcoded URL in `alembic.ini`. Workflow: change a SQLAlchemy model → `uv run alembic revision --autogenerate -m "..."` → hand-review the generated SQL (indexes on `business_id`, FKs, unique constraints, timestamps) → write and test the `downgrade()`. Never call `alembic upgrade head` from application startup code; it's a separate, explicit command.

## Billing and entitlement

- `services/billing.py` is the deep module (`start_checkout`, `sync_checkout`, `apply_event`, `open_portal`, `get_plan`, `summarize`, `access_for`). Stripe sits behind `integrations/stripe/port.py` (`BillingGateway`) with a real adapter (`adapter.py`) and `FakeBillingGateway` (`fake.py`); no Stripe SDK type leaves that package.
- **Every business-feature router (Slice 4 onwards) depends on `core.billing.get_entitled_tenant`, not `get_tenant`.** It returns 402 `subscription_required` unless the business is `trialing`, `active` or `past_due`. Only onboarding, `/me`, `/business` and `/billing/*` stay on `get_tenant`.
- Stripe is the source of truth. The webhook (`POST /api/v1/webhooks/stripe`, raw-body signature check, `stripe_events` dedupe) and the return-page sync both **re-fetch** the subscription from Stripe instead of trusting payloads, under a per-business row lock (`repositories/billing.py`).
- Local webhooks: `stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe`, then put the printed `whsec_...` in `STRIPE_WEBHOOK_SECRET`. Opt-in live smoke test: `STRIPE_LIVE_TESTS=1 uv run pytest -m live`.
- No card data ever reaches Tether: Checkout and the Billing Portal are Stripe-hosted, and the web app holds no Stripe key at all.

## Conversation engine and Gemini

- `services/conversation.py` `ConversationEngine` is the single entry point for every conversation turn (`handle_turn`, `end`, `transcript`); the voice slices reuse it with a Twilio adapter. It is stateless between calls: messages, entities, failures and fallback markers live in `interactions` / `interaction_messages` (`state` JSONB), never in process memory.
- Gemini proposes, the backend validates: the adapter returns raw text (`ModelReply`), the engine parses it against `schemas/agent_turn.AgentTurn`. Invalid output (bad JSON, schema miss, blank reply, a tool not in `services/tools.ToolRegistry`) gets exactly one retry, then a fixed fallback line; provider failure (after `LLM_RETRY`'s 2 attempts) or the per-turn deadline gets the provider fallback. A reached usage limit gets `quota_fallback(business)` (the business's own phone/email, no promised callback), makes zero model calls, sets `state.requires_human`, and is never retried. The two fallback constants plus `quota_fallback` and `no_info_fallback` in `services/conversation.py` are the only agent text that doesn't come from the model or the owner's greeting. Failures are recorded on the interaction as fixed reason codes, never raw model or customer text.
- Tool loop: tool requests travel inside the JSON reply (`AgentTurn.tool_calls`; Gemini 2.5 can't combine structured output with native function calling). A reply with tool calls is never shown: `services/tools.ToolExecutor` validates each call against the tool's Pydantic args model (`rejected` and never run on a miss), runs it, and writes an `interaction_operations` row for every call; then the engine checkpoints and rebuilds the request with FAQ RESULTS / TOOL RESULTS in the system instruction. At most `MAX_TOOL_ROUNDS` (3) rounds of `MAX_CALLS_PER_ROUND` (3) calls; a model still asking after that gets `no_info_fallback`. So one turn makes at most 5 model calls, all under the one turn deadline (which cancels model calls only, never DB work). The per-request registry is built in `core/conversation.py`; adding a tool is one registration (`gemini-tool` skill).
- Grounding: FAQs reach the model only through `search_faq` → `services/knowledge.KnowledgeBase.relevant` (active FAQs only; all of them when a business has ≤ `limit`, otherwise OR-semantics Postgres FTS on the generated `faqs.search_vector`; no match is `[]`, never the whole table). A final reply with an intent in `GROUNDED_INTENTS` (`FAQ`, `BUSINESS_HOURS`, `PRICE_INQUIRY`: facts only FAQs hold until the hours/services slices add their own tools) must cite `sources`, and every cited id (any intent) must be an FAQ retrieved in *this* turn, or it's replaced by `no_info_fallback(business)` (the business's contacts, no callback) and `state.requires_human` is set. The guard checks ids, not wording: the prompt rule is what keeps the text to the cited FAQ. Each tool executor runs inside `store.savepoint()`, so one that fails on a DB error rolls back only its own work and is still logged as `failed`.
- Transactions: the engine calls `store.checkpoint()` (a commit) before every model call, so no transaction or connection is held across it. The interaction row lock (`FOR UPDATE`, 3s `lock_timeout` → 409 `interaction_busy`) is taken only after the call, to allocate `sequence_number` and merge state.
- `services/prompt_builder.build(PromptContext)` puts platform rules first; owner settings and conversation-extracted data go after them inside delimited blocks, through the builder's sanitizers. Never interpolate owner or customer text into the system instruction any other way. Empty `PromptContext` slots (FAQs, services, hours, tools) are filled by later slices without changing callers.
- `integrations/llm/`: `LLMClient` port, `GeminiClient` (google-genai), `FakeLLMClient` (tests script replies turn by turn with `queue(...)` and `agent_turn(...)`; `len(llm.requests)` is the provider call count; fake tokens are 100 in / 20 out). The Gemini structured-output schema (`response_schema_for_gemini`) is hand-built and separate from `AgentTurn`: change both together. Adapter policy: **one attempt per `generate`**, failures raise `LLMUnavailableError(retryable=...)` (retryable only for timeouts, transport errors, 408, 429 and 5xx); blocked/empty/truncated replies are not retryable; token counts come from `usage_metadata` (thinking counts as output); never log prompts or model output. The engine retries with `retry_bounded(..., LLM_RETRY, is_retryable)` around `UsageGuard.metered`.
- Opt-in live smoke test against real Gemini: `GEMINI_API_KEY=... uv run pytest -m live`.

## FAQs and the operation log

- `/api/v1/faqs` (GET list + `max_faqs`, POST, PATCH, DELETE; entitled tenant): `services/faqs.py` over `repositories/faqs.SqlFaqStore`. Disabling is `PATCH {"active": false}`. Other tenants' ids are 404 `faq_not_found`. The per-business cap is serialized with `pg_advisory_xact_lock` before counting.
- `interaction_operations` is append-only: tool name, validated + truncated arguments (a rejected call stores only argument *names*), status `succeeded | rejected | failed`, a machine-summary `result` (ids, counts, reason codes), `duration_ms`. The inbox reads it later; never put model or customer free text in `result`.
- Seed (synthetic only): `uv run python -m seeds.demo_salon` creates "Demo Salon (synthetic)" with 15 FAQs (one inactive); `--owner-email you@...` adds them to your dev business instead. Idempotent by question. The live smoke test reuses `seeds.demo_salon.DEMO_SALON_FAQS`.

## Usage limits (`UsageGuard`)

- `services/usage.py` `UsageGuard` is the deep module: `metered(kind, *, business_id, interaction_id=None)` (async context manager yielding a `Meter`), `check_admission(ActiveCounts)`, `summary(business_id)`, `window()`. Limits (`UsageLimits`) and cost rates come from settings via `core/usage.py`; nothing else reads them. A kind with no `DailyLimit` (tokens, `billing_call`, `calendar_call`) is metered but never blocked.
- **Window:** the UTC calendar day for every scope, global and per-business. Documented in the panel as "resets 00:00 UTC".
- **Race safety:** `metered` reserves *before* the call with a conditional upsert per counter (`repositories/usage.py` `bump_counter`: `ON CONFLICT DO UPDATE ... WHERE count + n <= limit`), global first then business, in its own committed transaction (`core/usage.SqlUsageStore` binds a session *factory*, never the request session), so no lock is held across a provider call. `usage_counters` has one row per (scope, kind, day) via two partial unique indexes (`business_id IS NULL` = global). `usage_events` is the append-only audit log (PRD §10.1).
- A reserved attempt stays counted whether it succeeds or fails; a failure (or cancellation) also records `failed_request`, plus any tokens the provider reported anyway (`LLMUnavailableError.tokens_in/out` for blocked or truncated replies). Recording after the call is best effort (logged, bounded by `RECORD_TIMEOUT_SECONDS`), the reservation already enforced the limit.
- If the store fails while reserving: a capped kind fails **closed** (`UsageUnavailableError`, no provider call; the engine answers with the provider fallback, reason `usage_unavailable`), a metered-only kind fails **open** (the call proceeds, the gap is logged), so a usage-table hiccup never blocks checkout or webhooks.
- **Concurrency:** a *new* interaction is admitted only while recently active ones (status `active`, `updated_at` within `INTERACTION_IDLE_MINUTES`) are under both caps. The count runs under a transaction-level advisory lock in the request's transaction (`repositories/interactions.lock_admission_and_count`) and the insert follows in the same transaction; over the cap is 429 `concurrency_limited`. Continuing an existing chat is never refused for concurrency.
- Stripe: `core/billing.py` wraps the adapter in `services/billing_metering.MeteredBillingGateway` (`billing_call`, metered only, never capped; SDK retries stay as they are). `parse_webhook` is local and not metered.
- `GET /api/v1/usage` (entitled tenant) returns the business's counters vs. limits, tokens, failed requests, estimated cost (decimal string) and concurrency. Global usage is exposed **only** as `global_limit_reached` flags, never as numbers (they're other tenants' data).
- Tests: the `usage` fixture (`tests/conftest.py`, `UsageHarness`) is what the test app's `get_usage_guard` returns: generous limits and a clock pinned to a unique far-future UTC day, so DB tests never share counters with each other or consume the dev database's real global quota; tighten with `usage.set_daily(...)` / `usage.set_concurrency(...)`. Unit tests use `tests/services/memory_usage_store.py` (`MemoryUsageStore`, `guard()`, `limits()`).

## Still placeholders (land with the slice that needs them)

- `TTS_REQUEST`, `CALL`, `CALL_SECONDS`, `SMS` and `CALENDAR_CALL` are defined and configured in `UsageGuard` but nothing calls them yet; the voice, SMS and calendar slices must meter their adapters with them (and enforce `MAX_CALL_DURATION_SECONDS`). `TenantContext` landed in slice 3 (`core/tenant.py`).
- `integrations/clerk/` is the first provider client, behind `integrations/clerk/port.py` with a real JWKS adapter (`resolver.py`) and an in-memory fake (`fake.py`), per the root `CLAUDE.md`. `integrations/stripe/` (#28) and `integrations/llm/` (#4) follow the same shape. Twilio and Google Calendar still need their own.

## Testing

`uv run pytest` from inside `apps/api/` (with `ENVIRONMENT=local` or `ci`: `production` requires real provider keys at settings load). Shared fixtures in `tests/conftest.py`: `usage` (the isolated `UsageGuard` harness, see "Usage limits"), `two_tenants`, `entitled_tenants` (both on a trial via the fake billing gateway, for routes behind `get_entitled_tenant`), `billing_gateway`, `llm` (`FakeLLMClient`, already wired into `app`). Tests mirror the layers: unit tests for `services/` use fake callables (no DB); `repositories/` and endpoint-level `api/` tests that need a real Postgres read `DATABASE_URL` from the environment and are intentionally not skipped when it's unreachable — they should fail loudly, not pass silently.
