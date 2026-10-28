"""Slice 15b end to end on real Postgres: with `VOICE_INPUT_MODE=gemini_audio` the caller
is recorded (`<Record>`), the input webhook gets a `RecordingSid`, the recording is fetched
and transcribed (fakes for Twilio and Gemini), the transcript reaches the engine as text,
and no recording is left at Twilio: deleted after each turn, on failure paths too, and
swept when the call ends. Over the STT limit the caller is reprompted in the same turn
and the call continues on Twilio's speech recognition (`stt=1` in the signed URLs).
"""

# The slice-14 fixtures (`client`, `numbers`, ...) are imported by name, which pytest needs
# and ruff reads as redefinitions.
# ruff: noqa: F811

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from core.db import async_session_factory
from core.voice import (
    INPUT_PATH,
    STATUS_PATH,
    get_recordings_api,
    get_stt_client,
    get_voice_input_mode,
)
from integrations.llm.fake import agent_turn
from integrations.stt.fake import FakeSpeechToText
from integrations.twilio.fake import FakeRecordingsApi
from models.interaction import InteractionMessage
from services.call_session import NOT_HEARD_LINE, REPROMPT_LINE
from services.usage import UsageKind
from tests.api.test_voice import (  # noqa: F401  (fixtures used by name)
    GREETING,
    PUBLIC,
    Twiml,
    _associate,
    _call_sid,
    _counter,
    _greeting,
    _interaction,
    _signed,
    _start,
    client,
    numbers,
    twilio_configured,
    voice,
)

REPLY = "Happy to help with that. Would you like to book a visit?"
# Half a second of non-silent 8 kHz 16-bit audio after a WAV header.
SPOKEN = b"RIFF" + b"\x00" * 40 + b"\x01\x02" * 4000


@pytest.fixture
def audio_input(app) -> tuple[FakeSpeechToText, FakeRecordingsApi]:
    stt, recordings = FakeSpeechToText(), FakeRecordingsApi()
    app.dependency_overrides[get_voice_input_mode] = lambda: "gemini_audio"
    app.dependency_overrides[get_stt_client] = lambda: stt
    app.dependency_overrides[get_recordings_api] = lambda: recordings
    return stt, recordings


def _record_query(twiml: Twiml) -> dict[str, str]:
    """The <Record> action's query; the <Redirect> after it (silence) posts the same."""
    record = twiml.root.find("Record")
    assert record is not None, "expected <Record>"
    assert record.attrib["playBeep"] == "false"
    parsed = urlparse(record.attrib["action"])
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{PUBLIC}{INPUT_PATH}"
    redirect = twiml.root.find("Redirect")
    assert redirect is not None and redirect.text == record.attrib["action"]
    return {k: v[0] for k, v in parse_qs(parsed.query).items()}


async def _recorded(client, sid: str, query: dict, recording_sid: str | None) -> Twiml:
    form = {"CallSid": sid}
    if recording_sid is not None:
        form["RecordingSid"] = recording_sid
        # Never followed: the adapter builds the media URL from the sid on our account.
        form["RecordingUrl"] = "https://attacker.example/steal"
        form["RecordingDuration"] = "2"
    return Twiml(await _signed(client, INPUT_PATH, form, query))


async def _customer_messages(interaction_id) -> list[str]:
    async with async_session_factory() as session:
        rows = await session.scalars(
            select(InteractionMessage.message_text)
            .where(
                InteractionMessage.interaction_id == interaction_id,
                InteractionMessage.speaker == "customer",
            )
            .order_by(InteractionMessage.sequence_number)
        )
        return list(rows)


async def test_a_recorded_caller_is_understood_by_gemini_and_no_recording_is_kept(
    client, entitled_tenants, numbers, llm, usage, audio_input
):
    stt, recordings = audio_input
    tenant, other = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    await _greeting(client, tenant)
    sid = _call_sid()

    greeting = await _start(client, to, sid)
    assert greeting.said == GREETING and greeting.root.find("Gather") is None
    assert _record_query(greeting) == {"turn": "1"}

    # Silence: Twilio skips the <Record> action and follows the <Redirect>.
    quiet = await _recorded(client, sid, {"turn": 1}, None)
    assert quiet.said == REPROMPT_LINE
    assert _record_query(quiet) == {"turn": "1", "silences": "1"}

    stt.say("What are your opening hours? My card is 4242 4242 4242 4242")
    llm.queue(agent_turn(REPLY))
    recording = recordings.add(sid, SPOKEN)
    reply = await _recorded(client, sid, {"turn": 1, "silences": 1}, recording)

    assert reply.said == REPLY
    assert _record_query(reply) == {"turn": "2"}  # still recording
    interaction = await _interaction(sid)
    [said] = await _customer_messages(interaction.id)
    assert said.startswith("What are your opening hours?") and "4242" not in said
    assert stt.requests == [(len(SPOKEN), "audio/x-wav")]
    assert recordings.recordings == {} and recordings.deleted == [recording]
    assert ("fetch", recording) in recordings.calls  # by sid, never the payload URL

    # The call ends: the status callback sweeps anything a failed delete left behind.
    leftover = recordings.add(sid)
    status = await _signed(
        client, STATUS_PATH, {"CallSid": sid, "CallStatus": "completed", "CallDuration": "20"}
    )
    assert status.status_code == 204
    assert recordings.recordings == {} and recordings.deleted[-1] == leftover

    day = usage.day.date()
    assert await _counter(tenant.business_id, UsageKind.STT_REQUEST, day) == 1
    assert await _counter(other.business_id, UsageKind.STT_REQUEST, day) == 0
    # fetch + delete, list + delete for the sweep
    assert await _counter(tenant.business_id, UsageKind.TELEPHONY_API, day) >= 4


async def test_over_the_stt_limit_the_caller_is_reprompted_and_the_call_switches_to_twilio_stt(
    client, entitled_tenants, numbers, llm, usage, audio_input
):
    stt, recordings = audio_input
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    sid = _call_sid()
    await _start(client, to, sid)
    usage.set_daily(UsageKind.STT_REQUEST, per_day=1000, per_business_per_day=0)

    recording = recordings.add(sid, SPOKEN)
    fallback = await _recorded(client, sid, {"turn": 1}, recording)

    assert fallback.said == NOT_HEARD_LINE and not fallback.hangs_up
    assert fallback.root.find("Record") is None
    assert fallback.gather_query == {"turn": "1", "stt": "1"}  # same turn, Twilio's STT
    assert stt.requests == [] and recordings.recordings == {}

    llm.queue(agent_turn(REPLY))
    reply = Twiml(
        await _signed(
            client,
            INPUT_PATH,
            {"CallSid": sid, "SpeechResult": "When are you open?"},
            {"turn": 1, "stt": 1},
        )
    )
    assert reply.said == REPLY
    assert reply.gather_query == {"turn": "2", "stt": "1"}  # the fallback sticks


async def test_a_recording_posted_after_the_call_ended_is_still_deleted(
    client, entitled_tenants, numbers, llm, audio_input
):
    stt, recordings = audio_input
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    sid = _call_sid()
    await _start(client, to, sid)
    # The caller hangs up mid-<Record>: the status callback can win the race.
    await _signed(client, STATUS_PATH, {"CallSid": sid, "CallStatus": "completed"})

    recording = recordings.add(sid, SPOKEN)
    late = await _recorded(client, sid, {"turn": 1}, recording)

    assert late.hangs_up
    assert stt.requests == [] and recordings.deleted == [recording]


async def test_without_a_gemini_key_gemini_audio_mode_keeps_twilio_stt(
    app, client, entitled_tenants, numbers, audio_input
):
    app.dependency_overrides[get_stt_client] = lambda: None
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)

    greeting = await _start(client, to, _call_sid())

    assert greeting.root.find("Record") is None
    assert greeting.gather_query == {"turn": "1"}
