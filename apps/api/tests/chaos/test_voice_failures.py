"""Provider failures on a live call, over signed Twilio webhooks: a TTS failure falls back
to `<Say>` with bounded attempts and the call carries on; a Gemini failure is spoken as the
provider fallback, never a dropped call."""

# The slice-14 fixtures (`client`, `numbers`, ...) are imported by name, which pytest needs
# and ruff reads as redefinitions.
# ruff: noqa: F811

import pytest

from integrations.llm.fake import agent_turn
from integrations.tts.port import TTS_RETRY
from services.conversation import PROVIDER_FALLBACK
from services.usage import UsageKind
from tests.api.test_voice import (  # noqa: F401  (fixtures used by name)
    GREETING,
    _associate,
    _call_sid,
    _greeting,
    _say,
    _start,
    client,
    numbers,
    twilio_configured,
    voice,
)
from tests.chaos.chaos import FAULTS, Fault
from tests.chaos.support import CUSTOMER_CANARY, assert_logs_are_clean, tether_records

REPLY = "Happy to help with that. What would you like to book?"
FAULT_IDS = list(FAULTS)


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_a_tts_failure_is_said_not_played_and_the_call_continues(
    client, entitled_tenants, numbers, llm, chaos, tts, tether_logs, fault: Fault
):
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    await _greeting(client, tenant)
    chaos.fail("tts", fault)
    sid = _call_sid()

    greeting = await _start(client, to, sid)
    assert greeting.said == GREETING and greeting.root.find("Gather/Play") is None
    assert not greeting.hangs_up
    assert chaos.calls("tts") == TTS_RETRY.attempts  # one line, at most its retry budget

    llm.queue(agent_turn(REPLY))
    reply = await _say(client, sid, 1, f"I need a haircut {CUSTOMER_CANARY}")

    assert reply.said == REPLY and reply.root.find("Gather/Play") is None  # said, not played
    assert not reply.hangs_up and reply.gather_query == {"turn": "2"}  # the call goes on
    assert chaos.calls("tts") == 2 * TTS_RETRY.attempts
    assert tts.requests == []  # nothing was synthesized
    outcomes = [
        r.__dict__["outcome"]
        for r in tether_records(tether_logs, "tether.speech")
        if r.getMessage() == "tts"
    ]
    assert outcomes and all(isinstance(o, str) and o != "played" for o in outcomes)
    assert_logs_are_clean(tether_logs, CUSTOMER_CANARY)


async def test_a_reached_tts_limit_during_an_outage_makes_no_provider_call(
    client, entitled_tenants, numbers, chaos, usage
):
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    await _greeting(client, tenant)
    usage.set_daily(UsageKind.TTS_REQUEST, per_day=1_000_000, per_business_per_day=0)
    chaos.fail("tts", "server_error")

    greeting = await _start(client, to, _call_sid())

    assert greeting.said == GREETING and not greeting.hangs_up
    assert chaos.calls("tts") == 0


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_a_gemini_failure_on_a_call_is_spoken_as_the_fallback(
    client, entitled_tenants, numbers, llm, chaos, tts, fault: Fault
):
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    sid = _call_sid()
    await _start(client, to, sid)
    chaos.fail("gemini", fault)

    reply = await _say(client, sid, 1, "I need a haircut")

    # The fallback line is voiced like any reply (TTS is healthy here) and the call goes on.
    assert tts.requests[-1][0] == PROVIDER_FALLBACK and reply.root.find("Gather/Play") is not None
    assert not reply.hangs_up and reply.gather_query == {"turn": "2"}
    assert chaos.calls("gemini") == 2  # one retry, never more
    assert llm.requests == []
