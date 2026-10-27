"""Opt-in smoke test against Twilio with TEST credentials (never delivers, never billed).

    TWILIO_LIVE_TESTS=1 TWILIO_TEST_ACCOUNT_SID=AC... TWILIO_TEST_AUTH_TOKEN=... \
        uv run pytest -m live -n 0 tests/integrations/test_twilio_live.py

Uses Twilio's magic from-number +15005550006 (valid with test credentials). Never runs
by default and never reads the app's real TWILIO_* settings.
"""

import os

import pytest

from integrations.twilio.client import HttpTwilioSmsApi

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("TWILIO_LIVE_TESTS") != "1",
        reason="set TWILIO_LIVE_TESTS=1 (and TWILIO_TEST_* credentials) to hit Twilio",
    ),
]


async def test_live_send_with_test_credentials():
    sid = os.environ["TWILIO_TEST_ACCOUNT_SID"]
    token = os.environ["TWILIO_TEST_AUTH_TOKEN"]
    api = HttpTwilioSmsApi(sid, token, "+15005550006", 10.0)

    sent = await api.send_sms(to="+15005550006", body="Tether test message", status_callback=None)

    assert sent.sid.startswith("SM")
    assert sent.status in {"queued", "accepted", "sending", "sent"}
