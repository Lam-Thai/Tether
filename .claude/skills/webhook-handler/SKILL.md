---
name: webhook-handler
description: Add or change a Twilio (or other provider) webhook handler in apps/api. Use when a webhook needs signature validation, idempotency, or changes to what it persists.
---

# Webhook handler

External providers retry webhooks; the handler must tolerate that, and must respond fast since the provider is waiting on it.

## Steps

1. **Signature validation.** Validate the provider's signature (e.g. Twilio's `X-Twilio-Signature`) before touching the payload. An invalid signature is rejected before any other processing.
2. **Idempotency key.** Derive a key from the payload (e.g. Twilio's `CallSid`/`MessageSid` plus the event type) and check it against what's already been processed before doing anything else. A retried webhook with the same key is a no-op, not a duplicate booking/SMS/escalation.
3. **Fast response.** Persist the minimum needed to acknowledge the webhook and respond quickly; push slower follow-up work (AI processing, further provider calls) to a background task rather than blocking the webhook response.
4. **Metered outbound calls.** Any provider call the handler (or its background task) makes goes through `usage.metered(...)`, attributed to the business resolved from the stored row, never from the payload alone (`provider-adapter` skill).
5. **Persistence before side effects.** Write the event to the database before triggering anything externally visible (an SMS, a calendar write), so a crash between the two leaves a recoverable record instead of a silent miss.

## Checklist

- [ ] Signature validated before the payload is used for anything.
- [ ] Idempotency key checked; a repeat delivery is a no-op.
- [ ] Response returned fast; heavy work deferred to a background task.
- [ ] DB write happens before any external side effect, not after.
- [ ] Test covers a duplicate delivery of the same event.
