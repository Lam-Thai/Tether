"""`HttpGoogleCalendarApi` against `httpx.MockTransport`: no network, real request shapes."""

import json
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from integrations.google_calendar.client import HttpGoogleCalendarApi
from integrations.google_calendar.port import (
    REQUIRED_SCOPES,
    EventDraft,
    GoogleApiError,
    TimeWindow,
)

START = datetime(2026, 11, 2, 14, tzinfo=UTC)
END = datetime(2026, 11, 2, 15, tzinfo=UTC)
WINDOW = TimeWindow(START, END)
SECRET_VALUES = ("refresh-secret", "access-secret", "code-secret", "client-secret")


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def api_with(*responses: httpx.Response | Exception) -> tuple[HttpGoogleCalendarApi, Recorder]:
    recorder = Recorder(*responses)
    api = HttpGoogleCalendarApi(
        "client-id", "client-secret", 5, transport=httpx.MockTransport(recorder)
    )
    return api, recorder


def json_response(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def google_error(status: int, reason: str) -> httpx.Response:
    return json_response(
        status,
        {"error": {"code": status, "message": "provider text", "errors": [{"reason": reason}]}},
    )


def test_authorization_url_asks_for_offline_minimal_scopes_with_pkce():
    api, _ = api_with()
    url = api.authorization_url(state="st", code_challenge="ch", redirect_uri="https://app/cb")

    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert set(q["scope"].split()) == REQUIRED_SCOPES
    assert (q["access_type"], q["prompt"], q["include_granted_scopes"]) == (
        "offline",
        "consent",
        "false",
    )
    assert (q["code_challenge_method"], q["code_challenge"], q["state"]) == ("S256", "ch", "st")
    assert (q["client_id"], q["redirect_uri"], q["response_type"]) == (
        "client-id",
        "https://app/cb",
        "code",
    )
    assert "client-secret" not in url


async def test_exchange_code_posts_a_form_body_and_maps_the_grant():
    api, rec = api_with(
        json_response(
            200,
            {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": 3599,
                "scope": " ".join(sorted(REQUIRED_SCOPES)),
            },
        )
    )

    grant = await api.exchange_code(
        code="code-secret", code_verifier="ver", redirect_uri="https://a/cb"
    )

    request = rec.requests[0]
    assert (request.method, str(request.url)) == ("POST", "https://oauth2.googleapis.com/token")
    assert request.url.query == b""
    assert form(request) == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "grant_type": "authorization_code",
        "code": "code-secret",
        "code_verifier": "ver",
        "redirect_uri": "https://a/cb",
    }
    assert (grant.refresh_token, grant.access_token, grant.expires_in) == (
        "refresh-secret",
        "access-secret",
        3599,
    )
    assert grant.scopes == REQUIRED_SCOPES
    assert "secret" not in repr(grant)


async def test_exchange_without_a_refresh_token_maps_to_none():
    api, _ = api_with(json_response(200, {"access_token": "a", "scope": "x"}))
    grant = await api.exchange_code(code="c", code_verifier="v", redirect_uri="r")
    assert grant.refresh_token is None


async def test_refresh_posts_the_refresh_token_in_the_body():
    api, rec = api_with(json_response(200, {"access_token": "access-secret", "expires_in": 3600}))

    token = await api.refresh_access_token("refresh-secret")

    assert token.token == "access-secret"
    assert form(rec.requests[0])["grant_type"] == "refresh_token"
    assert form(rec.requests[0])["refresh_token"] == "refresh-secret"
    assert "refresh-secret" not in str(rec.requests[0].url)


async def test_list_calendars_follows_pages_and_uses_the_bearer_header():
    api, rec = api_with(
        json_response(
            200,
            {
                "items": [
                    {"id": "a@x", "summary": "A", "primary": True, "accessRole": "owner"},
                    {
                        "id": "b@x",
                        "summary": "B",
                        "summaryOverride": "Mine",
                        "accessRole": "reader",
                        "timeZone": "Europe/Paris",
                    },
                ],
                "nextPageToken": "page2",
            },
        ),
        json_response(200, {"items": [{"id": "c@x", "summary": "C", "accessRole": "owner"}]}),
    )

    calendars = await api.list_calendars("access-secret")

    assert [(c.id, c.name, c.primary, c.time_zone, c.access_role) for c in calendars] == [
        ("a@x", "A", True, None, "owner"),
        ("b@x", "Mine", False, "Europe/Paris", "reader"),
        ("c@x", "C", False, None, "owner"),
    ]
    first, second = rec.requests
    assert first.headers["Authorization"] == "Bearer access-secret"
    assert dict(first.url.params) == {"maxResults": "250"}
    assert dict(second.url.params) == {"maxResults": "250", "pageToken": "page2"}
    assert "access-secret" not in str(first.url)


async def test_free_busy_posts_the_window_and_maps_busy_intervals():
    api, rec = api_with(
        json_response(
            200,
            {
                "calendars": {
                    "cal@x": {
                        "busy": [
                            {"start": "2026-11-02T14:15:00Z", "end": "2026-11-02T14:30:00Z"},
                        ]
                    }
                }
            },
        )
    )

    busy = await api.free_busy("access-secret", "cal@x", WINDOW)

    assert [(b.start.minute, b.end.minute) for b in busy] == [(15, 30)]
    body = json.loads(rec.requests[0].content)
    assert body["items"] == [{"id": "cal@x"}]
    assert datetime.fromisoformat(body["timeMin"]) == START
    assert str(rec.requests[0].url) == "https://www.googleapis.com/calendar/v3/freeBusy"


async def test_free_busy_calendar_error_in_the_reply_is_not_found():
    api, _ = api_with(
        json_response(200, {"calendars": {"cal@x": {"errors": [{"reason": "notFound"}]}}})
    )
    with pytest.raises(GoogleApiError) as caught:
        await api.free_busy("t", "cal@x", WINDOW)
    assert caught.value.not_found


async def test_insert_event_url_encodes_the_calendar_id_and_sends_the_event_id():
    api, rec = api_with(
        json_response(
            200,
            {
                "id": "evt1",
                "htmlLink": "https://calendar.google.com/e",
                "start": {"dateTime": "2026-11-02T14:00:00Z"},
                "end": {"dateTime": "2026-11-02T15:00:00Z"},
            },
        )
    )
    draft = EventDraft("Haircut", START, END, description="d", time_zone="America/New_York")

    created = await api.insert_event("access-secret", "a b/c@x", draft, "evt1")

    request = rec.requests[0]
    assert request.url.raw_path == b"/calendar/v3/calendars/a%20b%2Fc%40x/events"
    body = json.loads(request.content)
    assert body["id"] == "evt1"
    assert body["summary"] == "Haircut"
    assert body["start"]["timeZone"] == "America/New_York"
    assert (created.id, created.html_link, created.start) == (
        "evt1",
        "https://calendar.google.com/e",
        START,
    )


async def test_insert_event_409_reads_the_existing_event_back():
    existing = {
        "id": "evt1",
        "start": {"dateTime": "2026-11-02T14:00:00Z"},
        "end": {"dateTime": "2026-11-02T15:00:00Z"},
    }
    api, rec = api_with(google_error(409, "duplicate"), json_response(200, existing))

    created = await api.insert_event("t", "cal", EventDraft("x", START, END), "evt1")

    assert created.id == "evt1"
    assert [r.method for r in rec.requests] == ["POST", "GET"]
    assert rec.requests[1].url.path.endswith("/events/evt1")


async def test_delete_event_accepts_204_and_maps_404():
    api, rec = api_with(httpx.Response(204), google_error(404, "notFound"))

    await api.delete_event("t", "cal", "evt1")
    assert rec.requests[0].method == "DELETE"
    with pytest.raises(GoogleApiError) as caught:
        await api.delete_event("t", "cal", "evt1")
    assert caught.value.not_found


async def test_revoke_sends_the_token_in_the_body_not_the_url():
    api, rec = api_with(httpx.Response(200, json={}))

    await api.revoke("refresh-secret")

    request = rec.requests[0]
    assert str(request.url) == "https://oauth2.googleapis.com/revoke"
    assert form(request) == {"token": "refresh-secret"}


async def test_revoke_of_an_already_revoked_token_is_success():
    api, _ = api_with(json_response(400, {"error": "invalid_token"}))
    await api.revoke("t")  # no raise


async def test_revoke_other_failures_raise():
    api, _ = api_with(json_response(503, {}))
    with pytest.raises(GoogleApiError) as caught:
        await api.revoke("t")
    assert caught.value.retryable


@pytest.mark.parametrize(
    ("response", "retryable", "auth_revoked", "not_found", "reason"),
    [
        (json_response(400, {"error": "invalid_grant"}), False, True, False, "invalid_grant"),
        (json_response(401, {"error": "invalid_client"}), False, False, False, "invalid_client"),
        (google_error(401, "authError"), False, True, False, "unauthorized"),
        (
            google_error(403, "insufficientPermissions"),
            False,
            True,
            False,
            "insufficient_permissions",
        ),
        (google_error(403, "rateLimitExceeded"), True, False, False, "rate_limited"),
        (google_error(403, "userRateLimitExceeded"), True, False, False, "rate_limited"),
        (google_error(403, "forbidden"), False, False, False, "forbidden"),
        (google_error(404, "notFound"), False, False, True, "not_found"),
        (google_error(408, "x"), True, False, False, "timeout"),
        (google_error(429, "x"), True, False, False, "rate_limited"),
        (httpx.Response(500, text="boom secret details"), True, False, False, "server_error"),
        (httpx.Response(503), True, False, False, "server_error"),
        (google_error(400, "badRequest"), False, False, False, "unexpected_status"),
        (httpx.Response(200, text="not json"), False, False, False, "bad_response"),
    ],
    ids=[
        "invalid_grant",
        "invalid_client",
        "401",
        "403-insufficient",
        "403-rate",
        "403-user-rate",
        "403-other",
        "404",
        "408",
        "429",
        "500",
        "503",
        "400",
        "malformed-200",
    ],
)
async def test_error_mapping(response, retryable, auth_revoked, not_found, reason):
    api, _ = api_with(response)

    with pytest.raises(GoogleApiError) as caught:
        await api.list_calendars("t")

    error = caught.value
    assert (error.retryable, error.auth_revoked, error.not_found) == (
        retryable,
        auth_revoked,
        not_found,
    )
    assert str(error) == reason  # a fixed code, never provider text


@pytest.mark.parametrize(
    ("failure", "reason"),
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("down"), "transport_error")],
    ids=["timeout", "transport"],
)
async def test_timeouts_and_transport_errors_are_retryable(failure, reason):
    api, _ = api_with(failure)
    with pytest.raises(GoogleApiError) as caught:
        await api.refresh_access_token("t")
    assert caught.value.retryable
    assert str(caught.value) == reason


async def test_nothing_secret_reaches_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    api, _ = api_with(
        json_response(
            200,
            {"access_token": "access-secret", "refresh_token": "refresh-secret", "scope": ""},
        ),
        json_response(200, {"items": []}),
        httpx.Response(200, json={}),
        httpx.Response(500, text="refresh-secret"),
    )

    await api.exchange_code(code="code-secret", code_verifier="v", redirect_uri="r")
    await api.list_calendars("access-secret")
    await api.revoke("refresh-secret")
    with pytest.raises(GoogleApiError):
        await api.revoke("refresh-secret")

    for secret in SECRET_VALUES:
        assert secret not in caplog.text
