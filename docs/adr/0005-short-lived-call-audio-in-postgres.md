# 5. Short-lived call audio in Postgres

- Status: Accepted
- Date: 2026-10-27
- Issue: #15

## Context

Callers hear a Gemini text-to-speech voice. Twilio plays audio by fetching a URL, so the API must serve the synthesized bytes at a public address for a few seconds. The API runs serverless (Vercel): there is no durable local filesystem, and a second request may land on a different instance than the one that synthesized the audio. State lives in Postgres (root `CLAUDE.md`), and a URL that works forever for call audio would leak what was said.

## Decision

- **The audio lives in Postgres as `bytea`**, in `media_objects` (capped at 5 MB by a CHECK), never on the filesystem or in memory. Any instance can serve any token.
- **Two tables, so every URL expires in minutes while the audio can be cached longer.** `media_objects` holds the bytes and an optional `cache_key` (partial UNIQUE on `(business_id, cache_key)`), so a greeting is synthesized once per day and not on every call. `media_tokens` holds one row per URL handed out, valid for `MEDIA_TOKEN_TTL_SECONDS` (300). Re-using a cached object mints a new token.
- **Tokens are 256-bit random (`secrets.token_urlsafe(32)`, 43 characters) and only `sha256(token)` is stored.** Reading the database never yields a working URL.
- **`GET`/`HEAD /api/v1/media/{token}` has no auth.** Twilio fetches it with no credentials, so the token is the capability. Malformed, unknown and expired tokens all answer the same 404 `media_not_found`. Responses are `Cache-Control: no-store` and `nosniff`; the route is not in the OpenAPI schema and nothing lists media.
- **Expiry uses the database clock (`now()`)**, so serverless clock skew can't extend a token. A token is live only while both it and its object are unexpired.
- **Each operation is its own committed transaction** (`core/media.SqlMediaRepository` binds a session factory), so the token is visible before Twilio fetches it, and no transaction is held across a provider call.
- **Cleanup is opportunistic.** Each `put` and each cache hit (`cached`) deletes up to 100 expired tokens and 100 expired objects across all businesses, best effort: a failure is logged as its type and never fails the call. Expired rows are never served (the read checks the database clock), so audio is unreachable the moment its TTL ends; physical deletion follows the platform's next call line. A scheduled purge is the follow-up if traffic ever gets too sparse for that.
- **Cache lookups are tenant-scoped** (`business_id`); token lookups are not, since the token is the capability. A business's deletion cascades to its media.

## Alternatives considered

- **Object storage (S3, R2, Vercel Blob).** Better for large media, but a new provider, credential and bill for a few kilobytes of short-lived audio, and the project is on a zero-spend plan.
- **The filesystem (`/tmp`).** Not shared between serverless instances, so Twilio's fetch could miss the audio.
- **One table with a long-lived token per cached object.** Simpler, but a leaked URL would work for the whole cache lifetime.
- **Signed stateless URLs carrying the audio's id.** No token table, but no way to revoke, and the same expiry could not be enforced from the database clock.

## Consequences

- Audio rows add database size and read traffic. The 5 MB cap and the short lifetime bound it; revisit with object storage if calls need long or high-bitrate audio.
- A cached object that is replaced changes what its still-live tokens serve; that is intended (the audio for a key is whatever was last synthesized).
- A token that was fetched once stays valid until it expires; Twilio may legitimately fetch twice (HEAD, then GET).
