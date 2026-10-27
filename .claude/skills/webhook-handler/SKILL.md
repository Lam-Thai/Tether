---
name: webhook-handler
description: Add or change a Twilio (or other provider) webhook handler in apps/api. Use when a webhook needs signature validation, idempotency, or changes to what it persists.
---

# Webhook handler

External providers retry webhooks; the handler must tolerate that, and must respond fast since the provider is waiting on it.

## Steps

1. **Signature validation.** Validate the provider's signature (e.g. Twilio's `X-Twilio-Signature`) before touching the payload. An invalid signature is rejected before any other processing. For Twilio, depend on `core.twilio.TwilioWebhookAuth` (a module-level instance, see `api/v1/twilio_webhooks.py`): it reads the raw form body (no `Form(...)` params: `python-multipart` isn't installed), checks the signature against `API_PUBLIC_BASE_URL + path` (Twilio signs the public URL it called, not the proxied one), and answers 403 `invalid_signature`, also when Twilio is unconfigured. Test with `integrations.twilio.signature.compute_signature` (`tests/api/test_twilio_webhooks.py`).
2. **Idempotency key.** Derive a key from the payload (e.g. Twilio's `CallSid`/`MessageSid` plus the event type) and check it against what's already been processed before doing anything else. A retried webhook with the same key is a no-op, not a duplicate booking/SMS/escalation. Status callbacks also arrive out of order: rank the statuses and only move forward (`services/escalation.SMS_STATUS_RANK`, a conditional UPDATE on the status you read). Resolve the business from the stored row the key points to, never from the payload; an unknown key is acknowledged (2xx) and ignored.
3. **Fast response.** Persist the minimum needed to acknowledge the webhook and respond quickly; push slower follow-up work (AI processing, further provider calls) to a background task rather than blocking the webhook response.
4. **Metered outbound calls.** Any provider call the handler (or its background task) makes goes through `usage.metered(...)`, attributed to the business resolved from the stored row, never from the payload alone (`provider-adapter` skill).
5. **Voice webhooks answer TwiML, and slow work is resumable** (`api/v1/twilio_webhooks.py`, `services/call_session.py`). The business comes from the stored row (the number called, then the interaction by CallSid), never the payload. Per-call progress (turn number, silence count, hold attempt) rides in the query string of URLs you rendered, which Twilio signs, never in memory. A turn that might outlast Twilio's ~15 s timeout runs shielded on its own DB session with a heartbeated claim (`voice_turns`); the webhook answers a holding phrase + `<Redirect>`, and the wait webhook returns the stored result or takes over a claim whose heartbeat went stale (a frozen serverless worker). The idempotency key (`turn_key`) is checked again where the result is stored, under the row lock, so racing workers store it once. Any unexpected error still answers valid TwiML (apology + `<Hangup/>`).
6. **Persistence before side effects.** Write the event to the database before triggering anything externally visible (an SMS, a calendar write), so a crash between the two leaves a recoverable record instead of a silent miss.

## Checklist

- [ ] Signature validated before the payload is used for anything.
- [ ] Idempotency key checked; a repeat delivery is a no-op.
- [ ] Response returned fast; heavy work deferred to a background task.
- [ ] DB write happens before any external side effect, not after.
- [ ] Test covers a duplicate delivery of the same event.
- [ ] Voice: TwiML on every path (errors too), progress in signed URLs, slow work resumable (hold + wait + takeover).
