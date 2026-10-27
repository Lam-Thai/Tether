---
name: provider-adapter
description: Add or change an outbound provider integration in apps/api (Twilio voice/SMS, Google Calendar, Gemini TTS, any new paid or quota'd API). Use when a slice makes a new external call, or changes how an existing one retries, is metered, or is limited.
---

# Provider adapter

Every outbound provider call is metered by `UsageGuard` and retried only by `retry_bounded`. The full design (window, race safety, concurrency, test harness) is `apps/api/CLAUDE.md` → "Usage limits"; read it first. This skill is the order of work.

## Steps

1. **Port + fake.** In `integrations/<provider>/`: `port.py` (a `Protocol` with plain dataclasses in and out; no SDK type leaves the package), `fake.py` (in-memory, scriptable, records every call so tests can count them), then the real adapter. Done when a service can depend on the port alone.
2. **One attempt per call.** The adapter makes exactly one provider attempt: SDK-level retries off, a per-attempt timeout from settings. Failures raise one domain error carrying `retryable` (true only for timeouts, transport errors, 408, 429, 5xx), mirroring `integrations/llm/port.LLMUnavailableError`. **Exception: a call with a side effect the provider can't deduplicate** (Twilio message create has no idempotency key): `retryable` only when the provider certainly did NOT take the request (connect errors, 429). A timeout, 5xx or unreadable 2xx is `send_unconfirmed`, never retried, because a retry could double-send (`integrations/twilio/client.py`). The exception is an SDK whose own retries attach idempotency keys (Stripe): keep those, bounded, and say so in the adapter docstring.
   Secrets (tokens, codes, client secrets) travel only in headers or POST form bodies, never in a URL (httpx logs URLs), and the error message is a fixed reason code, never provider text (`integrations/google_calendar/`).
3. **Usage kind.** Reuse an existing `UsageKind` (`tts_request`, `call`, `call_seconds`, `sms`, `calendar_call`) or add one: `services/usage.py` (`UsageKind` + `PROVIDER`), `models/usage.USAGE_KINDS`, and a migration that rewrites both check constraints (`ck_usage_events_event_type`, `ck_usage_counters_kind`). A kind that needs a cap gets settings for both scopes (`<KIND>_PER_DAY`, `<KIND>_PER_BUSINESS_PER_DAY`) wired in `core/usage.usage_limits`; add it to `PANEL_KINDS` if the owner should see it.
4. **Call site.** In the service, `retry_bounded(attempt, POLICY, is_retryable)` *around* `async with usage.metered(kind, business_id=..., interaction_id=...)`, the provider call inside. `business_id` comes from `TenantContext` or a row resolved from it; `None` only for genuinely tenant-less calls (it then meters globally). Report consumption on the meter where it exists. Done when every path to the provider passes through `metered`, retries included.
5. **Limit reached.** Catch `LimitExceededError` where the customer-facing outcome is decided and answer with a fixed safe fallback that offers human follow-up through details the backend actually holds. It is never retried and never claims an action happened. Record the reason on the interaction as a fixed code. Treat `UsageUnavailableError` (limits couldn't be checked, capped kinds only) like a provider outage: its fallback, no provider call. When the provider fails after spending something (tokens, seconds), carry it on the error and record it on the meter before re-raising.
6. **Zero cost and demo mode.** The owner pays for nothing. Every provider setting is optional; unconfigured means a demo implementation behind the same port (`services/notifier.LogNotifier`), never a crash. Prefer the provider's free test mode for an opt-in `live` test (Twilio test credentials, Stripe test keys), never in the default suite. A fake whose ids land in a UNIQUE column must generate random ids (`FakeSmsApi`), since parallel DB tests share one database.
7. **Tests at the boundary.** Unit: `MemoryUsageStore` + the provider fake: attempts counted, `failed_request` recorded, a reached limit makes zero provider calls. DB/API: the `usage` fixture (`usage.set_daily(...)`) for per-business vs. global behaviour across `entitled_tenants`. Combine a flow's assertions into one test (each DB test pays ~15s of tenant setup on Neon).

## Checklist

- [ ] Port, fake and adapter exist; services import only the port.
- [ ] Adapter is single-attempt with `retryable` flagged; retries come only from `retry_bounded`.
- [ ] Every call is inside `usage.metered`, with `retry_bounded` outside it.
- [ ] `LimitExceededError` → fixed fallback, no retry, no false confirmation.
- [ ] New kind: enum, `PROVIDER`, `USAGE_KINDS`, check-constraint migration, settings for both scopes.
- [ ] Tests assert provider call counts from the fake and counters from the store.
