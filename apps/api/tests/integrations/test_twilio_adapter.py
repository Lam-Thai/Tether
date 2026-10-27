"""`HttpTwilioSmsApi` against `httpx.MockTransport`: no network, real request shapes."""

import base64
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from integrations.twilio.client import HttpTwilioSmsApi
from integrations.twilio.port import TwilioApiError

SID = "AC" + "a" * 32
TOKEN = "auth-token-secret"
BODY = "Tether alert: secret-body-text"


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


def api_with(*responses: httpx.Response | Exception) -> tuple[HttpTwilioSmsApi, Recorder]:
    recorder = Recorder(*responses)
    api = HttpTwilioSmsApi(SID, TOKEN, "+15005550006", 5.0, transport=httpx.MockTransport(recorder))
    return api, recorder


def reply(http: int, **body) -> httpx.Response:
    return httpx.Response(http, json=body)


async def send(api: HttpTwilioSmsApi, callback: str | None = None):
    return await api.send_sms(to="+15551230000", body=BODY, status_callback=callback)


async def test_sends_a_form_post_with_basic_auth():
    api, rec = api_with(reply(201, sid="SM1", status="queued"))

    sent = await send(api, "https://api.example.test/cb")

    assert (sent.sid, sent.status) == ("SM1", "queued")
    request = rec.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    form = parse_qs(request.content.decode())
    assert form == {
        "To": ["+15551230000"],
        "From": ["+15005550006"],
        "Body": [BODY],
        "StatusCallback": ["https://api.example.test/cb"],
    }
    expected = base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert TOKEN not in str(request.url)


async def test_status_callback_is_omitted_when_none():
    api, rec = api_with(reply(201, sid="SM1", status="queued"))
    await send(api)
    assert "StatusCallback" not in parse_qs(rec.requests[0].content.decode())


@pytest.mark.parametrize(
    ("response", "reason", "retryable", "error_code"),
    [
        (reply(429, code=20429, message="x"), "rate_limited", True, "20429"),
        (reply(500, code=20500), "send_unconfirmed", False, "20500"),
        (httpx.Response(503, text="<html>"), "send_unconfirmed", False, None),
        (reply(400, code=21211, message="bad"), "invalid_number", False, "21211"),
        (reply(400, code=21614), "invalid_number", False, "21614"),
        (reply(400, code=21608), "unverified_number", False, "21608"),
        (reply(401, code=20003), "auth_failed", False, "20003"),
        (reply(403, code=20003), "auth_failed", False, "20003"),
        (reply(400, code=21610), "provider_error", False, "21610"),
        (reply(400, message="no code"), "provider_error", False, None),
        (httpx.Response(201, text="not json"), "send_unconfirmed", False, None),
        (reply(201, status="queued"), "send_unconfirmed", False, None),
    ],
    ids=[
        "429",
        "500",
        "503-html",
        "21211",
        "21614",
        "21608",
        "401",
        "20003",
        "other-code",
        "no-code",
        "2xx-not-json",
        "2xx-no-sid",
    ],
)
async def test_error_mapping(response, reason, retryable, error_code):
    api, _ = api_with(response)

    with pytest.raises(TwilioApiError) as caught:
        await send(api)

    assert caught.value.reason == reason
    assert caught.value.retryable is retryable
    assert caught.value.error_code == error_code
    assert str(caught.value) == reason  # fixed code, no provider text


@pytest.mark.parametrize(
    ("exc", "reason", "retryable"),
    [
        (httpx.ConnectError("boom"), "transport", True),
        (httpx.ConnectTimeout("boom"), "transport", True),
        (httpx.ReadTimeout("boom"), "send_unconfirmed", False),
        (httpx.RemoteProtocolError("boom"), "send_unconfirmed", False),
    ],
    ids=["connect", "connect-timeout", "read-timeout", "dropped"],
)
async def test_transport_failures(exc, reason, retryable):
    api, _ = api_with(exc)

    with pytest.raises(TwilioApiError) as caught:
        await send(api)

    assert (caught.value.reason, caught.value.retryable) == (reason, retryable)


async def test_nothing_sensitive_is_logged_or_in_errors(caplog):
    caplog.set_level(logging.DEBUG)
    api, _ = api_with(reply(400, code=21211, message=f"bad number {BODY}"))

    with pytest.raises(TwilioApiError) as caught:
        await send(api)

    assert BODY not in str(caught.value) and TOKEN not in str(caught.value)
    assert BODY not in caplog.text and TOKEN not in caplog.text
