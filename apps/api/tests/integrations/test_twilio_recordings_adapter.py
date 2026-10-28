"""`HttpTwilioRecordingsApi` against `httpx.MockTransport`: no network, real request shapes."""

import base64

import httpx
import pytest

from integrations.twilio.port import RecordingAudio, TwilioApiError
from integrations.twilio.recordings import MAX_AUDIO_BYTES, HttpTwilioRecordingsApi

SID = "AC" + "a" * 32
TOKEN = "auth-token-secret"
RE = "RE" + "c" * 32
CALL = "CA" + "d" * 32
BASE = f"https://api.twilio.com/2010-04-01/Accounts/{SID}"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt secret-audio"


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


def api_with(*responses: httpx.Response | Exception) -> tuple[HttpTwilioRecordingsApi, Recorder]:
    recorder = Recorder(*responses)
    api = HttpTwilioRecordingsApi(SID, TOKEN, 5.0, transport=httpx.MockTransport(recorder))
    return api, recorder


def reply(http: int, **body) -> httpx.Response:
    return httpx.Response(http, json=body)


def wav(content: bytes = WAV, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        200, content=content, headers={"content-type": "audio/x-wav", **(headers or {})}
    )


def assert_basic_auth(request: httpx.Request) -> None:
    expected = base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert TOKEN not in str(request.url)


async def test_fetch_builds_url_and_returns_audio():
    api, rec = api_with(wav())

    audio = await api.fetch(RE)

    assert audio == RecordingAudio(data=WAV, content_type="audio/x-wav")
    request = rec.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{BASE}/Recordings/{RE}.wav"
    assert_basic_auth(request)


async def test_fetch_defaults_content_type():
    api, _ = api_with(httpx.Response(200, content=WAV))
    audio = await api.fetch(RE)
    assert audio.content_type == "audio/x-wav"


async def test_fetch_strips_content_type_parameters():
    api, _ = api_with(wav(headers={"content-type": "audio/wav; charset=binary"}))
    assert (await api.fetch(RE)).content_type == "audio/wav"


async def test_redirect_to_other_host_does_not_forward_credentials():
    storage = "https://media.example-storage.test/signed/abc?sig=1"
    api, rec = api_with(
        httpx.Response(307, headers={"location": storage}),
        wav(),
    )

    audio = await api.fetch(RE)

    assert audio.data == WAV
    first, second = rec.requests
    assert_basic_auth(first)
    assert str(second.url) == storage
    assert "authorization" not in second.headers
    assert TOKEN not in str(second.url)


@pytest.mark.parametrize(
    "bad", ["", "RE123", "re" + "c" * 32, "RE" + "C" * 32, RE + "x", "../" + RE]
)
async def test_bad_sid_makes_no_request(bad):
    api, rec = api_with()

    with pytest.raises(TwilioApiError) as fetch_err:
        await api.fetch(bad)
    with pytest.raises(TwilioApiError) as delete_err:
        await api.delete(bad)

    assert fetch_err.value.reason == "not_found" and not fetch_err.value.retryable
    assert delete_err.value.reason == "not_found"
    assert rec.requests == []


async def test_oversized_declared_length_is_rejected():
    api, _ = api_with(wav(headers={"content-length": str(MAX_AUDIO_BYTES + 1)}))
    with pytest.raises(TwilioApiError) as err:
        await api.fetch(RE)
    assert err.value.reason == "provider_error" and not err.value.retryable


class _UndeclaredStream(httpx.AsyncByteStream):
    """A streamed body with no Content-Length, so only the read cap can stop it."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


async def test_oversized_streamed_body_without_a_declared_length_is_rejected():
    response = httpx.Response(
        200,
        stream=_UndeclaredStream(b"RIFF", b"\0" * MAX_AUDIO_BYTES),
        headers={"content-type": "audio/x-wav"},
    )
    assert "content-length" not in response.headers  # reaches `_read_capped`
    api, _ = api_with(response)
    with pytest.raises(TwilioApiError) as err:
        await api.fetch(RE)
    assert err.value.reason == "provider_error" and not err.value.retryable


async def test_body_at_the_cap_is_accepted():
    body = b"RIFF" + b"\0" * (MAX_AUDIO_BYTES - 4)
    api, _ = api_with(wav(body))
    assert len((await api.fetch(RE)).data) == MAX_AUDIO_BYTES


@pytest.mark.parametrize("body", [b"", b"<html>nope</html>", b"WAVE"])
async def test_non_riff_body_is_rejected(body):
    api, _ = api_with(wav(body))
    with pytest.raises(TwilioApiError) as err:
        await api.fetch(RE)
    assert err.value.reason == "provider_error" and not err.value.retryable


@pytest.mark.parametrize(
    ("status", "reason", "retryable"),
    [
        (404, "not_found", True),
        (429, "rate_limited", True),
        (500, "provider_error", True),
        (503, "provider_error", True),
        (401, "auth_failed", False),
        (400, "provider_error", False),
        (403, "provider_error", False),
    ],
)
async def test_fetch_status_mapping(status, reason, retryable):
    api, _ = api_with(reply(status, code=12345, message="secret provider text"))

    with pytest.raises(TwilioApiError) as err:
        await api.fetch(RE)

    assert err.value.reason == reason
    assert err.value.retryable is retryable
    assert "secret" not in str(err.value)


async def test_fetch_auth_code_on_other_status_is_auth_failed():
    api, _ = api_with(reply(403, code=20003))
    with pytest.raises(TwilioApiError) as err:
        await api.fetch(RE)
    assert err.value.reason == "auth_failed" and not err.value.retryable
    assert err.value.error_code == "20003"


async def test_fetch_transport_error_is_retryable():
    api, _ = api_with(httpx.ConnectTimeout("boom"))
    with pytest.raises(TwilioApiError) as err:
        await api.fetch(RE)
    assert err.value.reason == "transport" and err.value.retryable


async def test_delete_uses_delete_on_json_url():
    api, rec = api_with(httpx.Response(204))

    await api.delete(RE)

    request = rec.requests[0]
    assert request.method == "DELETE"
    assert str(request.url) == f"{BASE}/Recordings/{RE}.json"
    assert_basic_auth(request)


async def test_delete_200_is_ok():
    api, _ = api_with(httpx.Response(200))
    await api.delete(RE)


async def test_delete_404_counts_as_success():
    api, _ = api_with(reply(404, code=20404))
    await api.delete(RE)


@pytest.mark.parametrize(
    ("status", "reason", "retryable"),
    [
        (429, "rate_limited", True),
        (500, "provider_error", True),
        (502, "provider_error", True),
        (401, "auth_failed", False),
        (400, "provider_error", False),
    ],
)
async def test_delete_status_mapping(status, reason, retryable):
    api, _ = api_with(reply(status))

    with pytest.raises(TwilioApiError) as err:
        await api.delete(RE)

    assert err.value.reason == reason
    assert err.value.retryable is retryable


async def test_delete_transport_error_is_retryable():
    api, _ = api_with(httpx.ReadTimeout("boom"))
    with pytest.raises(TwilioApiError) as err:
        await api.delete(RE)
    assert err.value.reason == "transport" and err.value.retryable


async def test_list_for_call_requests_and_filters():
    other = "RE" + "e" * 32
    api, rec = api_with(
        reply(
            200,
            recordings=[
                {"sid": RE},
                {"sid": "bogus"},
                {"sid": "PN" + "a" * 32},
                {"nosid": 1},
                "junk",
                {"sid": other},
            ],
        )
    )

    assert await api.list_for_call(CALL) == [RE, other]

    request = rec.requests[0]
    assert request.method == "GET"
    assert request.url.path == f"/2010-04-01/Accounts/{SID}/Recordings.json"
    assert request.url.params["CallSid"] == CALL
    assert request.url.params["PageSize"] == "100"
    assert_basic_auth(request)


@pytest.mark.parametrize("bad", ["", "CA123", "CA" + "D" * 32, "AC" + "a" * 32, CALL + "0"])
async def test_list_for_call_bad_sid_returns_empty_without_request(bad):
    api, rec = api_with()
    assert await api.list_for_call(bad) == []
    assert rec.requests == []


async def test_list_for_call_non_list_payload_is_provider_error():
    api, _ = api_with(reply(200, recordings="nope"))
    with pytest.raises(TwilioApiError) as err:
        await api.list_for_call(CALL)
    assert err.value.reason == "provider_error"


@pytest.mark.parametrize(
    ("status", "reason", "retryable"),
    [
        (429, "rate_limited", True),
        (503, "provider_error", True),
        (401, "auth_failed", False),
        (404, "not_found", False),
        (400, "provider_error", False),
    ],
)
async def test_list_for_call_status_mapping(status, reason, retryable):
    api, _ = api_with(reply(status))
    with pytest.raises(TwilioApiError) as err:
        await api.list_for_call(CALL)
    assert err.value.reason == reason
    assert err.value.retryable is retryable


async def test_list_for_call_transport_error_is_retryable():
    api, _ = api_with(httpx.ConnectError("boom"))
    with pytest.raises(TwilioApiError) as err:
        await api.list_for_call(CALL)
    assert err.value.reason == "transport" and err.value.retryable


def test_recording_audio_repr_hides_bytes():
    text = repr(RecordingAudio(data=WAV, content_type="audio/x-wav"))
    assert "secret-audio" not in text
    assert "RIFF" not in text
    assert f"bytes={len(WAV)}" in text
