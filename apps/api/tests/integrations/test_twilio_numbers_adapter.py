"""`HttpTwilioNumbersApi` against `httpx.MockTransport`: no network, real request shapes."""

import base64
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from integrations.twilio.numbers import HttpTwilioNumbersApi
from integrations.twilio.port import TwilioApiError

SID = "AC" + "a" * 32
TOKEN = "auth-token-secret"
PN = "PN" + "b" * 32
PHONE = "+15551230000"
BASE = f"https://api.twilio.com/2010-04-01/Accounts/{SID}"


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


def api_with(*responses: httpx.Response | Exception) -> tuple[HttpTwilioNumbersApi, Recorder]:
    recorder = Recorder(*responses)
    api = HttpTwilioNumbersApi(SID, TOKEN, 5.0, transport=httpx.MockTransport(recorder))
    return api, recorder


def reply(http: int, **body) -> httpx.Response:
    return httpx.Response(http, json=body)


def number_body(**overrides) -> dict:
    body = {
        "sid": PN,
        "phone_number": PHONE,
        "friendly_name": "Front desk",
        "voice_url": "https://api.example.test/voice",
        "voice_method": "POST",
        "status_callback": "https://api.example.test/status",
        "status_callback_method": "POST",
    }
    body.update(overrides)
    return body


def assert_basic_auth(request: httpx.Request) -> None:
    expected = base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert TOKEN not in str(request.url)


async def test_account_detects_trial():
    api, rec = api_with(reply(200, type="Trial"), reply(200, type="Full"))

    assert (await api.account()).trial is True
    assert (await api.account()).trial is False

    request = rec.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{BASE}.json"
    assert_basic_auth(request)


async def test_list_numbers_requests_first_page_and_maps_fields():
    api, rec = api_with(
        reply(
            200,
            incoming_phone_numbers=[
                number_body(),
                number_body(
                    sid="PN" + "c" * 32,
                    friendly_name="",
                    voice_url="",
                    voice_method="",
                    status_callback="",
                    status_callback_method="",
                ),
            ],
        )
    )

    numbers = await api.list_numbers()

    request = rec.requests[0]
    assert request.method == "GET"
    assert request.url.path == f"/2010-04-01/Accounts/{SID}/IncomingPhoneNumbers.json"
    assert request.url.params["PageSize"] == "100"
    assert_basic_auth(request)
    first, second = numbers
    assert (first.sid, first.phone_number, first.friendly_name) == (PN, PHONE, "Front desk")
    assert first.voice_url == "https://api.example.test/voice"
    assert (first.voice_method, first.status_callback_method) == ("POST", "POST")
    assert first.status_callback == "https://api.example.test/status"
    assert second.friendly_name is None
    assert second.voice_url is None and second.voice_method is None
    assert second.status_callback is None and second.status_callback_method is None


async def test_list_numbers_without_the_list_is_a_provider_error():
    api, _ = api_with(reply(200, other=[]))

    with pytest.raises(TwilioApiError) as caught:
        await api.list_numbers()

    assert caught.value.reason == "provider_error"


async def test_get_number():
    api, rec = api_with(reply(200, **number_body()))

    number = await api.get_number(PN)

    assert number.sid == PN
    request = rec.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{BASE}/IncomingPhoneNumbers/{PN}.json"
    assert_basic_auth(request)


async def test_set_webhooks_posts_a_form():
    api, rec = api_with(reply(200, **number_body()))

    number = await api.set_webhooks(
        PN,
        voice_url="https://api.example.test/voice",
        status_callback="https://api.example.test/status",
    )

    assert number.voice_url == "https://api.example.test/voice"
    request = rec.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE}/IncomingPhoneNumbers/{PN}.json"
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert parse_qs(request.content.decode()) == {
        "VoiceUrl": ["https://api.example.test/voice"],
        "VoiceMethod": ["POST"],
        "StatusCallback": ["https://api.example.test/status"],
        "StatusCallbackMethod": ["POST"],
    }
    assert_basic_auth(request)


async def test_set_webhooks_empty_strings_clear_but_still_send_methods():
    api, rec = api_with(
        reply(200, **number_body(voice_url="", status_callback="")),
    )

    number = await api.set_webhooks(PN, voice_url="", status_callback="")

    form = parse_qs(request_body(rec), keep_blank_values=True)
    assert form == {
        "VoiceUrl": [""],
        "VoiceMethod": ["POST"],
        "StatusCallback": [""],
        "StatusCallbackMethod": ["POST"],
    }
    assert number.voice_url is None and number.status_callback is None


def request_body(rec: Recorder) -> str:
    return rec.requests[0].content.decode()


@pytest.mark.parametrize(
    "bad",
    ["", "PN123", "pn" + "b" * 32, "PN" + "B" * 32, PN + "/../x", PN + "?a=b", "AC" + "b" * 32],
    ids=["empty", "short", "lowercase-prefix", "uppercase-hex", "path", "query", "wrong-prefix"],
)
async def test_invalid_sid_makes_no_request(bad):
    api, rec = api_with()

    with pytest.raises(TwilioApiError) as caught:
        await api.get_number(bad)
    with pytest.raises(TwilioApiError):
        await api.set_webhooks(bad, voice_url="x", status_callback="y")

    assert caught.value.reason == "not_found"
    assert caught.value.retryable is False
    assert rec.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        number_body(sid=None),
        number_body(sid="nope"),
        number_body(phone_number=None),
        number_body(phone_number=""),
    ],
    ids=["no-sid", "bad-sid", "no-phone", "empty-phone"],
)
async def test_2xx_with_invalid_number_is_a_provider_error(payload):
    api, _ = api_with(reply(200, **payload))

    with pytest.raises(TwilioApiError) as caught:
        await api.get_number(PN)

    assert (caught.value.reason, caught.value.retryable) == ("provider_error", False)


async def test_2xx_not_json_is_a_provider_error():
    api, _ = api_with(httpx.Response(200, text="not json"))

    with pytest.raises(TwilioApiError) as caught:
        await api.get_number(PN)

    assert caught.value.reason == "provider_error"


@pytest.mark.parametrize(
    ("response", "reason", "retryable", "error_code"),
    [
        (reply(429, code=20429, message="x"), "rate_limited", True, "20429"),
        (reply(500, code=20500), "provider_error", True, "20500"),
        (httpx.Response(503, text="<html>"), "provider_error", True, None),
        (reply(401, code=20003), "auth_failed", False, "20003"),
        (reply(403, code=20003), "auth_failed", False, "20003"),
        (reply(404, code=20404), "not_found", False, "20404"),
        (httpx.Response(404, text="gone"), "not_found", False, None),
        (reply(400, code=20404), "not_found", False, "20404"),
        (reply(400, code=21610), "provider_error", False, "21610"),
        (reply(400, message="no code"), "provider_error", False, None),
    ],
    ids=[
        "429",
        "500",
        "503-html",
        "401",
        "20003",
        "404",
        "404-no-json",
        "20404",
        "other-code",
        "no-code",
    ],
)
async def test_error_mapping(response, reason, retryable, error_code):
    api, _ = api_with(response)

    with pytest.raises(TwilioApiError) as caught:
        await api.get_number(PN)

    assert caught.value.reason == reason
    assert caught.value.retryable is retryable
    assert caught.value.error_code == error_code
    assert str(caught.value) == reason  # fixed code, no provider text


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("boom"),
        httpx.ConnectTimeout("boom"),
        httpx.ReadTimeout("boom"),
        httpx.RemoteProtocolError("boom"),
    ],
    ids=["connect", "connect-timeout", "read-timeout", "dropped"],
)
async def test_transport_failures_are_retryable(exc):
    api, _ = api_with(exc)

    with pytest.raises(TwilioApiError) as caught:
        await api.list_numbers()

    assert (caught.value.reason, caught.value.retryable) == ("transport", True)


async def test_nothing_sensitive_is_logged_or_in_errors(caplog):
    caplog.set_level(logging.DEBUG)
    api, _ = api_with(reply(400, code=21610, message=f"bad {PHONE} secret provider text"))

    with pytest.raises(TwilioApiError) as caught:
        await api.get_number(PN)

    for text in (str(caught.value), caplog.text):
        assert PHONE not in text
        assert "secret provider text" not in text
        assert TOKEN not in text
