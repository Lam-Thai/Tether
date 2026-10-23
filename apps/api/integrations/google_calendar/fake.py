"""In-memory `GoogleCalendarApi` for tests (and slices 10-11's booking tests).

Scriptable, and it records every call. Nothing here touches the network.

    api = FakeGoogleCalendarApi()
    refresh = api.issue_grant(calendars=[fake_calendar("work", "Work")])  # next exchange_code
    ...
    api.add_busy(refresh, "work", start, end)       # busy block the next free_busy returns
    api.revoke_externally(refresh)                  # the owner revoked access at Google
    api.fail_next("list_calendars", GoogleApiError("server_error", retryable=True), times=2)

`calls` is the call log (operation name first, then non-secret arguments); the
operation names are the port's method names. Token strings are fake values, and
`revoked` holds every token passed to `revoke`.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from integrations.google_calendar.port import (
    REQUIRED_SCOPES,
    AccessToken,
    BusyInterval,
    CalendarInfo,
    CreatedEvent,
    EventDraft,
    GoogleApiError,
    OAuthGrant,
    TimeWindow,
    authorization_url,
)

_AUTO = object()


def fake_calendar(
    calendar_id: str,
    name: str,
    *,
    primary: bool = False,
    access_role: str = "owner",
    time_zone: str | None = "America/New_York",
) -> CalendarInfo:
    return CalendarInfo(calendar_id, name, primary, time_zone, access_role)


@dataclass
class _Account:
    calendars: list[CalendarInfo]
    busy: dict[str, list[BusyInterval]] = field(default_factory=lambda: defaultdict(list))
    events: dict[tuple[str, str], CreatedEvent] = field(default_factory=dict)


@dataclass
class _QueuedGrant:
    refresh_token: str | None
    scopes: frozenset[str]
    token_key: str
    account: str
    calendars: list[CalendarInfo]


class FakeGoogleCalendarApi:
    def __init__(self, client_id: str = "fake-client-id") -> None:
        self.client_id = client_id
        self.calls: list[tuple[str, ...]] = []
        self.revoked: set[str] = set()
        # Google grants are per account (and client): every refresh token issued for one
        # account shares it, and revoking any of them revokes them all.
        self._accounts: dict[str, _Account] = {}
        self._token_account: dict[str, str] = {}  # refresh token -> account key
        self._dead: set[str] = set()  # revoked refresh tokens
        self._access: dict[str, str] = {}  # access token -> refresh token
        self._grants: list[_QueuedGrant] = []
        self._failures: dict[str, list[GoogleApiError]] = defaultdict(list)
        self._counter = 0

    # --- scripting ---------------------------------------------------------------

    def issue_grant(
        self,
        *,
        scopes: frozenset[str] | set[str] = REQUIRED_SCOPES,
        calendars: list[CalendarInfo] | None = None,
        refresh_token: str | None | object = _AUTO,
        account: str | None = None,
    ) -> str | None:
        """Scripts what the next `exchange_code` returns; returns the refresh token it
        will carry (`None` when scripted without one).
        Grants with the same `account` share one Google grant (default: a fresh account).
        Unscripted, a call gets a full
        grant with a single primary owner calendar."""
        self._counter += 1
        token = f"fake-refresh-{self._counter}" if refresh_token is _AUTO else refresh_token
        assert token is None or isinstance(token, str)
        self._grants.append(
            _QueuedGrant(
                refresh_token=token,
                scopes=frozenset(scopes),
                token_key=token or f"fake-no-refresh-{self._counter}",
                account=account or f"fake-account-{self._counter}",
                calendars=list(calendars)
                if calendars is not None
                else [fake_calendar("primary@example.com", "Main", primary=True)],
            )
        )
        return token

    def set_calendars(self, refresh_token: str, calendars: list[CalendarInfo]) -> None:
        self._of(refresh_token).calendars = list(calendars)

    def add_busy(
        self, refresh_token: str, calendar_id: str, start: datetime, end: datetime
    ) -> None:
        self._of(refresh_token).busy[calendar_id].append(BusyInterval(start, end))

    def events(self, refresh_token: str) -> dict[tuple[str, str], CreatedEvent]:
        """Events created through this account, keyed by (calendar id, event id)."""
        return self._of(refresh_token).events

    def revoke_externally(self, refresh_token: str) -> None:
        """The owner removed Tether's access at Google: refresh and API calls now fail."""
        self._kill_grant(refresh_token)

    def fail_next(self, op: str, error: GoogleApiError, *, times: int = 1) -> None:
        """Makes the next `times` calls of port method `op` raise `error`."""
        self._failures[op].extend([error] * times)

    @property
    def access_tokens(self) -> list[str]:
        """Every access token issued so far, for "never leaked" assertions."""
        return list(self._access)

    def calls_to(self, op: str) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] == op]

    # --- the port ----------------------------------------------------------------

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return authorization_url(
            self.client_id, state=state, code_challenge=code_challenge, redirect_uri=redirect_uri
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthGrant:
        self._begin("exchange_code")
        if self._grants:
            grant = self._grants.pop(0)
        else:
            self.issue_grant()
            grant = self._grants.pop(0)
        account = self._accounts.setdefault(grant.account, _Account(calendars=grant.calendars))
        account.calendars = grant.calendars
        self._token_account[grant.token_key] = grant.account
        self._dead.discard(grant.token_key)
        access = self._new_access(grant.token_key)
        return OAuthGrant(grant.refresh_token, access, 3600, grant.scopes)

    async def refresh_access_token(self, refresh_token: str) -> AccessToken:
        self._begin("refresh_access_token")
        if refresh_token not in self._token_account or refresh_token in self._dead:
            raise GoogleApiError("invalid_grant", auth_revoked=True)
        return AccessToken(self._new_access(refresh_token), 3600)

    async def list_calendars(self, access_token: str) -> list[CalendarInfo]:
        self._begin("list_calendars")
        return list(self._account(access_token).calendars)

    async def free_busy(
        self, access_token: str, calendar_id: str, window: TimeWindow
    ) -> list[BusyInterval]:
        self._begin("free_busy", calendar_id)
        account = self._account(access_token)
        self._require_calendar(account, calendar_id)
        return [
            b
            for b in account.busy.get(calendar_id, [])
            if b.start < window.end and b.end > window.start
        ]

    async def insert_event(
        self, access_token: str, calendar_id: str, draft: EventDraft, event_id: str
    ) -> CreatedEvent:
        self._begin("insert_event", calendar_id, event_id)
        account = self._account(access_token)
        self._require_calendar(account, calendar_id)
        existing = account.events.get((calendar_id, event_id))
        if existing is not None:  # Google's 409 duplicate, read back
            return existing
        created = CreatedEvent(
            id=event_id,
            html_link=f"https://calendar.example.com/event/{event_id}",
            start=draft.start,
            end=draft.end,
        )
        account.events[(calendar_id, event_id)] = created
        return created

    async def delete_event(self, access_token: str, calendar_id: str, event_id: str) -> None:
        self._begin("delete_event", calendar_id, event_id)
        account = self._account(access_token)
        if account.events.pop((calendar_id, event_id), None) is None:
            raise GoogleApiError("not_found", not_found=True)

    async def revoke(self, token: str) -> None:
        self._begin("revoke")
        self.revoked.add(token)
        refresh = self._access.get(token, token)
        if refresh in self._token_account:
            self._kill_grant(refresh)

    # --- internals ---------------------------------------------------------------

    def _begin(self, op: str, *args: str) -> None:
        self.calls.append((op, *args))
        if self._failures[op]:
            raise self._failures[op].pop(0)

    def _new_access(self, refresh_token: str) -> str:
        self._counter += 1
        access = f"fake-access-{self._counter}"
        self._access[access] = refresh_token
        return access

    def _account(self, access_token: str) -> _Account:
        refresh = self._access.get(access_token)
        if refresh is None or refresh in self._dead or refresh not in self._token_account:
            raise GoogleApiError("unauthorized", auth_revoked=True)
        return self._of(refresh)

    def _of(self, refresh_token: str) -> _Account:
        return self._accounts[self._token_account[refresh_token]]

    def _kill_grant(self, refresh_token: str) -> None:
        """Revoking one token kills every refresh token of that Google account's grant."""
        account = self._token_account[refresh_token]
        self._dead.update(t for t, a in self._token_account.items() if a == account)

    @staticmethod
    def _require_calendar(account: _Account, calendar_id: str) -> None:
        if all(c.id != calendar_id for c in account.calendars):
            raise GoogleApiError("not_found", not_found=True)
