"""The real Google Calendar adapter, over `httpx` (no Google SDK).

One attempt per call, a per-attempt timeout from settings. Retrying and metering
belong to the caller (`services/calendar.GoogleCalendarGateway`). Secrets only ever
travel in POST form bodies or the `Authorization` header, never in a URL, so httpx's
request logging (which prints URLs) cannot leak them; nothing here logs tokens, codes
or response bodies, and error messages are fixed reason codes.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from integrations.google_calendar.port import (
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

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
API_BASE = "https://www.googleapis.com/calendar/v3"

_PAGE_SIZE = 250
_MAX_PAGES = 20  # 5000 calendars; stops a misbehaving pager
_RATE_LIMIT_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "backendError"}
)
_PERMISSION_REASONS = frozenset({"insufficientPermissions", "authError"})


class _Conflict(Exception):
    """409 on an event insert: the client-chosen id already exists."""


class HttpGoogleCalendarApi:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return authorization_url(
            self._client_id,
            state=state,
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthGrant:
        body = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            }
        )
        try:
            return OAuthGrant(
                refresh_token=body.get("refresh_token") or None,
                access_token=str(body["access_token"]),
                expires_in=int(body.get("expires_in", 3600)),
                scopes=frozenset(str(body.get("scope", "")).split()),
            )
        except (KeyError, TypeError, ValueError):
            raise GoogleApiError("bad_response") from None

    async def refresh_access_token(self, refresh_token: str) -> AccessToken:
        body = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        try:
            return AccessToken(str(body["access_token"]), int(body.get("expires_in", 3600)))
        except (KeyError, TypeError, ValueError):
            raise GoogleApiError("bad_response") from None

    async def list_calendars(self, access_token: str) -> list[CalendarInfo]:
        calendars: list[CalendarInfo] = []
        page_token: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {"maxResults": _PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token
            body = await self._api("GET", "/users/me/calendarList", access_token, params=params)
            try:
                for item in body.get("items", []):
                    calendars.append(
                        CalendarInfo(
                            id=str(item["id"]),
                            name=str(item.get("summaryOverride") or item.get("summary") or ""),
                            primary=bool(item.get("primary", False)),
                            time_zone=item.get("timeZone"),
                            access_role=str(item.get("accessRole", "")),
                        )
                    )
            except (KeyError, TypeError, AttributeError):
                raise GoogleApiError("bad_response") from None
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        return calendars

    async def free_busy(
        self, access_token: str, calendar_id: str, window: TimeWindow
    ) -> list[BusyInterval]:
        body = await self._api(
            "POST",
            "/freeBusy",
            access_token,
            json={
                "timeMin": window.start.isoformat(),
                "timeMax": window.end.isoformat(),
                "items": [{"id": calendar_id}],
            },
        )
        try:
            entry = body["calendars"][calendar_id]
            errors = entry.get("errors")
            if errors:
                missing = str(errors[0].get("reason", "")) == "notFound"
                raise GoogleApiError(
                    "not_found" if missing else "freebusy_error", not_found=missing
                )
            return [
                BusyInterval(_parse_time(b["start"]), _parse_time(b["end"]))
                for b in entry.get("busy", [])
            ]
        except GoogleApiError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            raise GoogleApiError("bad_response") from None

    async def insert_event(
        self, access_token: str, calendar_id: str, draft: EventDraft, event_id: str
    ) -> CreatedEvent:
        path = f"/calendars/{_quote(calendar_id)}/events"
        payload: dict[str, Any] = {
            "id": event_id,
            "summary": draft.summary,
            "start": _event_time(draft.start, draft.time_zone),
            "end": _event_time(draft.end, draft.time_zone),
        }
        if draft.description:
            payload["description"] = draft.description
        try:
            body = await self._api("POST", path, access_token, json=payload, conflict_ok=True)
        except _Conflict:
            # The id exists: an earlier attempt already created this event.
            body = await self._api("GET", f"{path}/{_quote(event_id)}", access_token)
        try:
            return CreatedEvent(
                id=str(body["id"]),
                html_link=body.get("htmlLink"),
                start=_parse_time(body["start"]["dateTime"]),
                end=_parse_time(body["end"]["dateTime"]),
            )
        except (KeyError, TypeError, ValueError):
            raise GoogleApiError("bad_response") from None

    async def delete_event(self, access_token: str, calendar_id: str, event_id: str) -> None:
        path = f"/calendars/{_quote(calendar_id)}/events/{_quote(event_id)}"
        await self._api("DELETE", path, access_token)

    async def revoke(self, token: str) -> None:
        # The token goes in the form body, never the URL.
        response = await self._send("POST", REVOKE_ENDPOINT, data={"token": token})
        if response.status_code == 200:
            return
        if response.status_code == 400 and _oauth_error(response) == "invalid_token":
            return  # already revoked or expired: the goal is met
        raise _error_for(response)

    async def _token_request(self, form: Mapping[str, str]) -> dict[str, Any]:
        response = await self._send(
            "POST",
            TOKEN_ENDPOINT,
            data={"client_id": self._client_id, "client_secret": self._client_secret, **form},
        )
        if response.status_code != 200:
            raise _error_for(response)
        return _json_object(response)

    async def _api(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        conflict_ok: bool = False,
    ) -> dict[str, Any]:
        response = await self._send(
            method,
            API_BASE + path,
            params=params,
            json=json,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if conflict_ok and response.status_code == 409:
            raise _Conflict
        if response.status_code >= 300:
            raise _error_for(response)
        if response.status_code == 204 or not response.content:
            return {}
        return _json_object(response)

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.TimeoutException:
            raise GoogleApiError("timeout", retryable=True) from None
        except httpx.HTTPError:
            raise GoogleApiError("transport_error", retryable=True) from None


def _quote(value: str) -> str:
    return quote(value, safe="")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("naive datetime from Google")
    return parsed


def _event_time(moment: datetime, time_zone: str | None) -> dict[str, str]:
    value = {"dateTime": moment.isoformat()}
    if time_zone:
        value["timeZone"] = time_zone
    return value


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise GoogleApiError("bad_response") from None
    if not isinstance(body, dict):
        raise GoogleApiError("bad_response")
    return body


def _oauth_error(response: httpx.Response) -> str | None:
    """The `error` code of an OAuth endpoint reply (`invalid_grant`, ...), if any."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) else None


def _api_reason(response: httpx.Response) -> str | None:
    """The first `reason` of a Calendar API error reply (`rateLimitExceeded`, ...), if any."""
    try:
        return str(response.json()["error"]["errors"][0]["reason"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _error_for(response: httpx.Response) -> GoogleApiError:
    status = response.status_code
    oauth_error = _oauth_error(response)
    if oauth_error == "invalid_grant":
        return GoogleApiError("invalid_grant", auth_revoked=True)
    if oauth_error == "invalid_client":
        # Our own client id/secret is wrong: a server misconfiguration, not the owner's grant.
        return GoogleApiError("invalid_client")
    if status == 401:
        return GoogleApiError("unauthorized", auth_revoked=True)
    if status == 403:
        reason = _api_reason(response)
        if reason in _PERMISSION_REASONS:
            return GoogleApiError("insufficient_permissions", auth_revoked=True)
        if reason in _RATE_LIMIT_REASONS:
            return GoogleApiError("rate_limited", retryable=True)
        return GoogleApiError("forbidden")
    if status == 404:
        return GoogleApiError("not_found", not_found=True)
    if status in (408, 429):
        return GoogleApiError("rate_limited" if status == 429 else "timeout", retryable=True)
    if status >= 500:
        return GoogleApiError("server_error", retryable=True)
    return GoogleApiError("unexpected_status")
