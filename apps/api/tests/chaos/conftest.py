"""Fixtures for the provider-failure tests: every provider fake sits behind `ProviderChaos`.

`chaos` wires the fakes into the `app` fixture: Gemini (the `llm` fixture), Google Calendar
(the `calendar` fixture's API), Twilio SMS (`core.twilio.get_sms_api`) and TTS
(`core.speech.get_tts_client`). A test tells it what to break and reads the attempt counts
back; `llm`, `calendar.api`, `sms` and `tts` stay the plain fakes for scripting and for
asserting what really got through.

Retry backoff is not slept (the delays are recorded in `chaos.sleeps`): only
`core.retry`'s own `asyncio` reference is replaced, so the voice poll loops keep real time.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import core.retry
from core.booking_rules import default_booking_rules
from core.calendar import get_clock
from core.llm import get_llm_client
from core.speech import get_tts_client
from core.twilio import get_sms_api
from integrations.tts.fake import FakeTtsClient
from integrations.twilio.fake import FakeSmsApi
from services.booking_rules import BookingRules
from tests.api.test_bookings import NOW
from tests.chaos.chaos import ProviderChaos


class _AsyncioWithoutSleep:
    """`asyncio` as `core.retry` sees it: `sleep` recorded, everything else the real module
    (patching `asyncio.sleep` itself would also freeze the voice poll loops)."""

    def __init__(self, sleep: Callable[[float], Awaitable[None]]) -> None:
        self.sleep = sleep

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


@pytest.fixture
def sms() -> FakeSmsApi:
    return FakeSmsApi()


@pytest.fixture
def tts() -> FakeTtsClient:
    return FakeTtsClient()


@pytest.fixture
def chaos(app, llm, calendar, sms, tts, monkeypatch) -> ProviderChaos:
    harness = ProviderChaos()

    async def record_instead_of_sleeping(seconds: float) -> None:
        harness.sleeps.append(seconds)

    monkeypatch.setattr(core.retry, "asyncio", _AsyncioWithoutSleep(record_instead_of_sleeping))

    chaotic_llm = harness.wrap("gemini", llm)
    chaotic_sms = harness.wrap("twilio_sms", sms)
    chaotic_tts = harness.wrap("tts", tts)
    calendar.api = harness.wrap("calendar", calendar.api)
    app.dependency_overrides[get_llm_client] = lambda: chaotic_llm
    app.dependency_overrides[get_sms_api] = lambda: chaotic_sms
    app.dependency_overrides[get_tts_client] = lambda: chaotic_tts
    return harness


@pytest.fixture
async def client(app):
    """The chat/API client with the booking tests' pinned clock and booking rules."""
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[default_booking_rules] = lambda: BookingRules(15, 120, 60, 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def tether_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="tether")
    return caplog
