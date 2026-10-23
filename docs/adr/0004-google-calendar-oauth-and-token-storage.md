# 4. Google Calendar OAuth and encrypted token storage

- Status: Accepted
- Date: 2026-10-22
- Issue: #9

## Context

Booking (Slices 10-11) needs offline access to each owner's Google Calendar. That means Tether holds a long-lived Google refresh token per business: it's the most sensitive credential the database stores. The OAuth flow itself also has to resist cross-site request forgery, the API authenticates with Clerk bearer tokens (which a browser redirect can't carry), and the PRD (§13.3) asks for encrypted refresh tokens, minimal scopes and no tokens on the frontend.

## Decision

- **The OAuth redirect lands on the web app, not the API.** `GET /calendar/connect` returns the Google URL as JSON (like Stripe Checkout). Google redirects to `/dashboard/settings/calendar/callback`, and that page sends `code` + `state` to `POST /api/v1/calendar/callback` with the owner's bearer. The API accepts the state only if its business and user match the bearer's. A flow one person starts can't be finished in someone else's session (login CSRF).
- **The state is a signed, expiring HS256 JWT** (`OAUTH_STATE_SECRET`, `aud` bound to this flow, 10-minute default TTL) carrying the business id, user id and a nonce. **PKCE without server-side state:** the code verifier is an HMAC of the nonce under the state secret, so the server can recompute it, and someone who intercepts code + state still can't redeem the code.
- **Minimal scopes:** `calendar.calendarlist.readonly`, `calendar.freebusy`, `calendar.events.owned`. Google can't scope a grant to one calendar, so the picker only offers calendars the owner owns. A grant missing a required scope is rejected (400).
- **Never revoke a token we're replacing or rejecting.** A Google grant belongs to one Google account and OAuth client, and revoking any token kills the whole grant. So revoking the old token on reconnect, or a rejected new one, would also kill the working token for the same account and leave the owner in a reconnect loop. A reconnect simply overwrites the stored token.
- **Refresh tokens are sealed with AES-256-GCM** (`services/token_vault.TokenVault`). Each row stores the key id. The associated data binds the ciphertext to its business, so a ciphertext copied into another row won't decrypt. Keys come from `CALENDAR_TOKEN_KEYS`, and the first is active. To rotate, add a new key first; each row is re-sealed under it after its next successful status probe. Access tokens are never persisted; one is refreshed per gateway operation.
- **Read-call-write updates are conditional on `updated_at`.** A probe or calendar choice that raced a reconnect or disconnect in another tab never overwrites it.
- **The calendar feature is off unless fully configured.** Without client credentials, token keys and the state secret, the calendar endpoints return 503 `calendar_not_configured`. A token is never stored unencrypted, and a missing key never stops the rest of the app from starting.
- **Disconnect always deletes the stored token.** It revokes at Google first. If Google can't confirm, the response says `revoked: false` and the owner is pointed at their Google account permissions.
- **Revocation shows up as a status.** An `invalid_grant` (or an unreadable ciphertext) marks the connection `reconnect_needed` and persists it, and the settings page shows Reconnect needed. Fixed reason codes are kept in `last_error`, never provider text.

## Alternatives considered

- **Google redirects straight to the API callback.** Simpler, but the API would trust the state alone, which is open to login CSRF.
- **Fernet instead of AES-GCM.** Fernet is equally safe, but it has no associated data to bind a ciphertext to its business, and `MultiFernet` hides which key sealed a row.
- **A `oauth_states` table for single-use state and the PKCE verifier.** Stronger replay protection, but Google's codes are already single-use, and the tenant/bearer binding covers the CSRF case.
- **The `calendar.events` scope.** It would allow shared calendars, but it grants writes on every calendar the owner can edit.

## Consequences

- Slices 10-11 reach Google only through `services/calendar.CalendarGateway`. Tests compose `GoogleCalendarGateway` over `FakeGoogleCalendarApi`, so metering, refresh and error mapping run for real.
- The Google Cloud app stays in Testing mode, where refresh tokens expire after 7 days. Owners will see Reconnect needed weekly until the app is verified, and that exercises the revoked-token path.
- Losing `CALENDAR_TOKEN_KEYS` makes every connection `reconnect_needed`. It doesn't leak anything; owners reconnect.
- Two Tether businesses connected to the same Google account share one grant, so disconnecting one revokes the other's access. That shows up there as `reconnect_needed`. It's rare for this product, and nothing fixes it short of a separate OAuth client per business.
- A replaced or rejected token that is never revoked stays valid at Google until it expires or the owner removes Tether's access. Tether no longer holds it.
