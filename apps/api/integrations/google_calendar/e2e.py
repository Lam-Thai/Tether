"""Google Calendar for the Playwright E2E run (`E2E_FAKE_PROVIDERS=1`, never production).

`FakeGoogleCalendarApi` with Google's real consent URL shape (`https://accounts.google.com/...`,
the only host the web client follows). No Google is contacted: the Playwright journey
intercepts that URL and redirects to its `redirect_uri` with `code=E2E_CODE` and the same
`state`, so the real "Connect Google Calendar" button and callback page run unchanged and
the callback page POSTs the code and state to `POST /calendar/callback`. Every code
exchange issues a full grant (all required scopes, a refresh token) with exactly one
selectable owner calendar.

Accounts, events and tokens live in this process, so restarting the E2E API makes a stored
connection `reconnect_needed` (reconnect through the UI); the `calendar_connections` row
itself is in Postgres like in production.
"""

from integrations.google_calendar.fake import FakeGoogleCalendarApi, fake_calendar
from integrations.google_calendar.port import OAuthGrant

E2E_CALENDAR_ID = "e2e-owner@example.com"
E2E_CALENDAR_NAME = "E2E Calendar"
E2E_CODE = "e2e-fake-code"


class E2EGoogleCalendarApi(FakeGoogleCalendarApi):
    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthGrant:
        # A fresh account per exchange: reconnecting never reuses a revoked grant.
        self.issue_grant(
            calendars=[fake_calendar(E2E_CALENDAR_ID, E2E_CALENDAR_NAME, primary=True)]
        )
        return await super().exchange_code(
            code=code, code_verifier=code_verifier, redirect_uri=redirect_uri
        )
