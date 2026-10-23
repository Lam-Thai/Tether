"""The Google Calendar port: everything `services/calendar.py` may ask of Google.

Plain dataclasses in, plain dataclasses out, so no HTTP detail leaves
`integrations/google_calendar/`. This is the raw provider: it takes and returns
PLAINTEXT tokens, makes exactly ONE attempt per call and does no metering. The
service-level `GoogleCalendarGateway` (`services/calendar.py`) decrypts stored
tokens, refreshes access tokens, wraps every call in `retry_bounded` around
`UsageGuard.metered`, and maps `GoogleApiError` to domain errors.

`HttpGoogleCalendarApi` is the real adapter; `FakeGoogleCalendarApi` the in-memory
stand-in for tests.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlencode

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"

SCOPE_CALENDAR_LIST = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
SCOPE_FREEBUSY = "https://www.googleapis.com/auth/calendar.freebusy"
SCOPE_EVENTS = "https://www.googleapis.com/auth/calendar.events.owned"
# Minimal on purpose: list the owner's calendars, read busy time, write our own events.
REQUIRED_SCOPES = frozenset({SCOPE_CALENDAR_LIST, SCOPE_FREEBUSY, SCOPE_EVENTS})


class GoogleApiError(Exception):
    """A Google call failed. The message is a fixed reason code (e.g. `invalid_grant`,
    `timeout`, `server_error`), never provider text, so it is always safe to log.

    `retryable`: timeouts, transport errors, 408, 429, 5xx, rate-limit 403s.
    `auth_revoked`: the owner's grant is gone or insufficient (invalid_grant, 401,
    insufficientPermissions); only reconnecting helps.
    `not_found`: 404 on a calendar or event.
    """

    def __init__(
        self,
        reason: str,
        *,
        retryable: bool = False,
        auth_revoked: bool = False,
        not_found: bool = False,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.auth_revoked = auth_revoked
        self.not_found = not_found


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, GoogleApiError) and exc.retryable


@dataclass(frozen=True)
class OAuthGrant:
    refresh_token: str | None  # absent when Google did not issue one
    access_token: str
    expires_in: int
    scopes: frozenset[str]

    def __repr__(self) -> str:  # keep tokens out of logs and test output
        return f"OAuthGrant(scopes={sorted(self.scopes)})"


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_in: int

    def __repr__(self) -> str:
        return "AccessToken(...)"


@dataclass(frozen=True)
class CalendarInfo:
    id: str
    name: str
    primary: bool
    time_zone: str | None
    access_role: str  # "owner" | "writer" | "reader" | "freeBusyReader"


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("A time window needs timezone-aware datetimes.")
        if not self.start < self.end:
            raise ValueError("A time window must start before it ends.")


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class EventDraft:
    summary: str
    start: datetime
    end: datetime
    description: str | None = None
    time_zone: str | None = None  # IANA name; the instants are what matter


@dataclass(frozen=True)
class CreatedEvent:
    id: str
    html_link: str | None
    start: datetime
    end: datetime


def authorization_url(client_id: str, *, state: str, code_challenge: str, redirect_uri: str) -> str:
    """The consent URL. Local string building; no call is made."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(sorted(REQUIRED_SCOPES)),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        }
    )
    return f"{AUTH_ENDPOINT}?{query}"


class GoogleCalendarApi(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthGrant: ...

    async def refresh_access_token(self, refresh_token: str) -> AccessToken: ...

    async def list_calendars(self, access_token: str) -> list[CalendarInfo]:
        """Every calendar on the user's list (all pages), whatever the access role."""
        ...

    async def free_busy(
        self, access_token: str, calendar_id: str, window: TimeWindow
    ) -> list[BusyInterval]: ...

    async def insert_event(
        self, access_token: str, calendar_id: str, draft: EventDraft, event_id: str
    ) -> CreatedEvent:
        """Creates the event under the caller-chosen `event_id`. Google answers 409 for an
        id it already holds, so a retried insert is safe: the adapter then reads the
        existing event back (one extra GET inside the same attempt) and returns it."""
        ...

    async def delete_event(self, access_token: str, calendar_id: str, event_id: str) -> None: ...

    async def revoke(self, token: str) -> None:
        """Revokes a refresh or access token. Already revoked counts as success."""
        ...
