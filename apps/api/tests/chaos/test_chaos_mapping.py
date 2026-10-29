"""`ProviderChaos` stays honest: its fault table matches what the REAL adapters raise.

Each real adapter runs over `httpx.MockTransport` (or a stub Gemini SDK) answering a
timeout, a 429 and a 503, and the error it raises must classify the same way as the error
the chaos table injects (retryable, rate limited, reason code). If an adapter's
classification changes, this fails before a provider-failure test starts asserting the
wrong retry behavior. The last tests cover the wrapper itself.
"""

from datetime import UTC, datetime

import httpx
import pytest
from google.genai import errors as genai_errors

from integrations.google_calendar.client import HttpGoogleCalendarApi
from integrations.google_calendar.fake import FakeGoogleCalendarApi
from integrations.google_calendar.port import GoogleApiError, TimeWindow
from integrations.llm.gemini import GeminiClient
from integrations.llm.port import LLMUnavailableError
from integrations.stt.gemini import GeminiSpeechToText
from integrations.stt.port import SttUnavailableError
from integrations.tts.fake import FakeTtsClient
from integrations.tts.gemini import GeminiTtsClient
from integrations.tts.port import TtsUnavailableError
from integrations.twilio.client import HttpTwilioSmsApi
from integrations.twilio.fake import FakeNumbersApi, FakeSmsApi
from integrations.twilio.numbers import HttpTwilioNumbersApi
from integrations.twilio.port import TwilioApiError
from tests.chaos.chaos import CANARY, FAULTS, PROVIDERS, Fault, ProviderChaos
from tests.integrations.test_gemini_adapter import REQUEST, stub_sdk

SID = "AC" + "a" * 32
WINDOW = TimeWindow(datetime(2026, 10, 9, 9, tzinfo=UTC), datetime(2026, 10, 9, 17, tzinfo=UTC))
FAULT_IDS = list(FAULTS)


def _transport(fault: Fault) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if fault == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(429 if fault == "rate_limited" else 503, json={})

    return httpx.MockTransport(handler)


def _sdk_error(fault: Fault) -> Exception:
    body = {"error": {"message": "provider text"}}
    if fault == "timeout":
        return TimeoutError()
    if fault == "rate_limited":
        return genai_errors.ClientError(429, body)
    return genai_errors.ServerError(503, body)


def _same_flags(real: Exception, injected: Exception, *flags: str) -> None:
    for flag in flags:
        assert getattr(real, flag) == getattr(injected, flag), flag


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_calendar_faults_match_the_real_adapter(fault: Fault):
    api = HttpGoogleCalendarApi("id", "secret", 5, transport=_transport(fault))
    with pytest.raises(GoogleApiError) as real:
        await api.free_busy("token", "primary", WINDOW)

    injected = ProviderChaos().error_for("calendar", fault)

    assert isinstance(injected, GoogleApiError)
    _same_flags(real.value, injected, "reason", "retryable", "auth_revoked", "not_found")


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_sms_faults_match_the_real_adapter(fault: Fault):
    api = HttpTwilioSmsApi(SID, "token", "+15005550006", 5, transport=_transport(fault))
    with pytest.raises(TwilioApiError) as real:
        await api.send_sms(to="+15551234567", body="alert", status_callback=None)

    injected = ProviderChaos().error_for("twilio_sms", fault)

    assert isinstance(injected, TwilioApiError)
    _same_flags(real.value, injected, "reason", "retryable")


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_numbers_faults_match_the_real_adapter(fault: Fault):
    api = HttpTwilioNumbersApi(SID, "token", 5, transport=_transport(fault))
    with pytest.raises(TwilioApiError) as real:
        await api.account()

    injected = ProviderChaos().error_for("twilio_numbers", fault)

    assert isinstance(injected, TwilioApiError)
    _same_flags(real.value, injected, "reason", "retryable")


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_gemini_faults_match_the_real_adapter(fault: Fault):
    sdk, _ = stub_sdk(_sdk_error(fault))
    client = GeminiClient(api_key="k", model="m", timeout_seconds=5, sdk_client=sdk)
    with pytest.raises(LLMUnavailableError) as real:
        await client.generate(REQUEST)

    injected = ProviderChaos().error_for("gemini", fault)

    assert isinstance(injected, LLMUnavailableError)
    _same_flags(real.value, injected, "retryable", "rate_limited")


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_tts_faults_match_the_real_adapter(fault: Fault):
    sdk, _ = stub_sdk(_sdk_error(fault))
    client = GeminiTtsClient(api_key="k", model="m", timeout_seconds=5, sdk_client=sdk)
    with pytest.raises(TtsUnavailableError) as real:
        await client.synthesize("hello", "Kore")

    injected = ProviderChaos().error_for("tts", fault)

    assert isinstance(injected, TtsUnavailableError)
    _same_flags(real.value, injected, "retryable", "rate_limited")


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_stt_faults_match_the_real_adapter(fault: Fault):
    sdk, _ = stub_sdk(_sdk_error(fault))
    client = GeminiSpeechToText(api_key="k", model="m", timeout_seconds=5, sdk_client=sdk)
    with pytest.raises(SttUnavailableError) as real:
        await client.transcribe(b"RIFF", "audio/wav")

    injected = ProviderChaos().error_for("stt", fault)

    assert isinstance(injected, SttUnavailableError)
    _same_flags(real.value, injected, "retryable", "rate_limited")


def test_the_table_covers_every_provider_and_fault():
    chaos = ProviderChaos()
    for provider in PROVIDERS:
        for fault in FAULTS:
            assert isinstance(chaos.error_for(provider, fault), Exception)


def test_the_canary_is_in_the_cause_never_the_message():
    chaos = ProviderChaos()
    for provider in PROVIDERS:
        for fault in FAULTS:
            error = chaos.error_for(provider, fault)
            assert CANARY not in str(error)
            assert CANARY in str(error.__cause__)


async def test_times_counts_attempts_and_heal_stops_the_faults():
    chaos = ProviderChaos()
    sms = chaos.wrap("twilio_sms", FakeSmsApi())
    chaos.fail("twilio_sms", "rate_limited", times=2)

    for _ in range(2):
        with pytest.raises(TwilioApiError) as raised:
            await sms.send_sms(to="+15551234567", body="x", status_callback=None)
        assert raised.value.retryable is True
    sent = await sms.send_sms(to="+15551234567", body="x", status_callback=None)

    assert sent.sid.startswith("SM")
    assert chaos.calls("twilio_sms") == 3  # the injected failures count as attempts

    chaos.fail("twilio_sms", "timeout")  # every call until healed
    for _ in range(3):
        with pytest.raises(TwilioApiError):
            await sms.send_sms(to="+15551234567", body="x", status_callback=None)
    chaos.heal("twilio_sms")
    await sms.send_sms(to="+15551234567", body="x", status_callback=None)
    assert chaos.calls("twilio_sms") == 7


async def test_an_op_filter_spares_other_operations():
    chaos = ProviderChaos()
    api = chaos.wrap("calendar", FakeGoogleCalendarApi())
    chaos.fail("calendar", "server_error", op="insert_event")

    # free_busy is not insert_event: it reaches the fake, which rejects the unknown token.
    with pytest.raises(GoogleApiError) as untouched:
        await api.free_busy("unknown-token", "primary", WINDOW)
    assert untouched.value.reason == "unauthorized"
    with pytest.raises(GoogleApiError) as injected:
        await api.insert_event("unknown-token", "primary", None, "event")  # type: ignore[arg-type]
    assert injected.value.reason == "server_error"
    assert (chaos.calls("calendar", "free_busy"), chaos.calls("calendar", "insert_event")) == (1, 1)
    assert chaos.calls("calendar") == 2

    with pytest.raises(ValueError):
        chaos.fail("calendar", "timeout", op="send_sms")


async def test_lands_runs_the_work_then_loses_the_answer():
    chaos = ProviderChaos()
    inner = FakeTtsClient()
    tts = chaos.wrap("tts", inner)
    chaos.fail("tts", "timeout", times=1, lands=True)

    with pytest.raises(TtsUnavailableError):
        await tts.synthesize("hello", "Kore")

    assert inner.requests == [("hello", "Kore")]  # the provider did get and run the request


async def test_the_numbers_proxy_reaches_scripted_state():
    chaos = ProviderChaos()
    fake = FakeNumbersApi()
    number = fake.add_number()
    numbers = chaos.wrap("twilio_numbers", fake)
    chaos.fail("twilio_numbers", "timeout", times=1)

    with pytest.raises(TwilioApiError):
        await numbers.get_number(number.sid)
    assert (await numbers.get_number(number.sid)).sid == number.sid
    assert numbers.numbers is fake.numbers  # non-port attributes pass straight through
