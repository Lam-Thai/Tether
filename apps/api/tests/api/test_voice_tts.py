"""Slice 15 end to end on real Postgres: the owner picks and previews a voice
(`/agent/voices`, `PATCH /agent`, `/agent/voice-preview`), then a call over signed Twilio
webhooks is answered with `<Play>` of short-lived media URLs that Twilio fetches from
`GET /api/v1/media/{token}`. The fake TTS client stands in for Gemini; a TTS failure or a
reached TTS limit falls back to `<Say>` in the same turn; expired media is 404.
"""

# The slice-14 fixtures (`client`, `numbers`, ...) are imported by name, which pytest needs
# and ruff reads as redefinitions.
# ruff: noqa: F811

import wave
from io import BytesIO
from urllib.parse import urlparse

import pytest
from sqlalchemy import text

from core.db import async_session_factory
from core.media import MEDIA_PATH
from core.speech import get_tts_client
from integrations.llm.fake import agent_turn
from integrations.tts.fake import FakeTtsClient
from integrations.tts.port import TtsUnavailableError
from services.usage import UsageKind
from tests.api.test_voice import (  # noqa: F401  (fixtures used by name)
    GREETING,
    PUBLIC,
    Twiml,
    _associate,
    _auth,
    _call_sid,
    _counter,
    _greeting,
    _say,
    _start,
    client,
    numbers,
    twilio_configured,
    voice,
)

REPLY = "Happy to help with that. What would you like to book?"


@pytest.fixture
def tts(app) -> FakeTtsClient:
    fake = FakeTtsClient()
    app.dependency_overrides[get_tts_client] = lambda: fake
    return fake


def _played(twiml: Twiml) -> list[str]:
    """The media paths of every `<Play>`, checked to be on this API's public origin."""
    paths = []
    for play in twiml.root.iter("Play"):
        url = urlparse(play.text or "")
        assert f"{url.scheme}://{url.netloc}" == PUBLIC
        assert url.path.startswith(f"{MEDIA_PATH}/")
        paths.append(url.path)
    return paths


async def _fetch_audio(client, path: str) -> bytes:
    response = await client.get(path)  # what Twilio does: no auth, the token is the key
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    data = response.content
    assert data[:4] == b"RIFF" and int.from_bytes(data[20:22], "little") == 7  # mu-law
    return data


async def test_the_caller_hears_the_chosen_gemini_voice_with_say_as_the_fallback(
    client, entitled_tenants, numbers, llm, usage, tts
):
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    await _greeting(client, tenant)
    patched = await client.patch("/api/v1/agent", json={"voice": "Puck"}, headers=_auth(tenant))
    assert patched.status_code == 200 and patched.json()["voice"] == "Puck"
    sid = _call_sid()

    # The greeting: played, not said, from a short-lived URL Twilio can fetch.
    greeting = await _start(client, to, sid)
    [greeting_path] = _played(greeting)
    assert greeting.said == "" and greeting.gather_query == {"turn": "1"}
    assert greeting.root.find("Gather/Play") is not None  # the caller can barge in
    await _fetch_audio(client, greeting_path)
    assert tts.requests == [(GREETING, "Puck")]

    # A reply: synthesized for this turn in the same voice.
    llm.queue(agent_turn(REPLY))
    hours = await _say(client, sid, 1, "I need a haircut")
    [reply_path] = _played(hours)
    assert hours.said == "" and hours.gather_query == {"turn": "2"}
    await _fetch_audio(client, reply_path)
    assert tts.requests[-1] == (REPLY, "Puck")

    # A TTS failure: the same reply is said with <Say>, in the same turn.
    tts.fail_next(TtsUnavailableError("bad request"))
    llm.queue(agent_turn("Sure, which day works for you?"))
    located = await _say(client, sid, 2, "Can I book one?")
    assert _played(located) == [] and located.said == "Sure, which day works for you?"
    assert located.gather_query == {"turn": "3"}

    # A second call reuses the cached greeting: no new TTS request, a NEW token.
    before = len(tts.requests)
    second = await _start(client, to, _call_sid())
    [second_greeting] = _played(second)
    assert second_greeting != greeting_path
    assert len(tts.requests) == before

    # The day's TTS limit is reached: replies fall back to <Say>, no provider call.
    day = usage.day.date()
    used = await _counter(tenant.business_id, UsageKind.TTS_REQUEST, day)
    assert used == 3  # greeting, first reply, the failed attempt
    usage.set_daily(UsageKind.TTS_REQUEST, per_day=1000, per_business_per_day=used)
    llm.queue(agent_turn("Anything else?"))
    limited = await _say(client, sid, 3, "Thanks")
    assert _played(limited) == [] and limited.said == "Anything else?"
    assert len(tts.requests) == before

    # Audio doesn't outlive its token: expired URLs are 404, with nothing to tell them apart.
    async with async_session_factory() as session:
        await session.execute(
            text(
                "UPDATE media_tokens SET expires_at = now() - interval '1 second' "
                "WHERE business_id = :b"
            ),
            {"b": tenant.business_id},
        )
        await session.commit()
    for path in (greeting_path, reply_path):
        gone = await client.get(path)
        assert gone.status_code == 404
        assert gone.json() == {
            "error": {"code": "media_not_found", "message": gone.json()["error"]["message"]}
        }


async def test_the_standard_voice_keeps_say(client, entitled_tenants, numbers, tts):
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    await _greeting(client, tenant)
    patched = await client.patch("/api/v1/agent", json={"voice": "standard"}, headers=_auth(tenant))
    assert patched.status_code == 200 and patched.json()["voice"] == "standard"

    greeting = await _start(client, to, _call_sid())

    assert greeting.said == GREETING and _played(greeting) == []
    assert tts.requests == []


async def test_the_owner_lists_picks_and_previews_voices(client, entitled_tenants, usage, tts):
    tenant, other = entitled_tenants
    voices = await client.get("/api/v1/agent/voices", headers=_auth(tenant))
    assert voices.status_code == 200
    body = voices.json()
    assert body["tts_configured"] is True and body["default_voice"] == "Kore"
    ids = [v["id"] for v in body["voices"]]
    assert ids[-1] == "standard" and "Kore" in ids and "Puck" in ids
    agent = await client.get("/api/v1/agent", headers=_auth(tenant))
    assert agent.json()["voice"] == "Kore"  # the default until the owner picks one

    bad = await client.patch("/api/v1/agent", json={"voice": "Nobody"}, headers=_auth(tenant))
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "unknown_voice"
    assert (await client.get("/api/v1/agent", headers=_auth(tenant))).json()["voice"] == "Kore"

    preview = await client.post(
        "/api/v1/agent/voice-preview", json={"voice": "Puck"}, headers=_auth(tenant)
    )
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"] == "audio/wav"
    with wave.open(BytesIO(preview.content)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 8000)
    # The SAVED greeting of THIS business, metered against THIS business.
    assert tts.requests == [(agent.json()["greeting"], "Puck")]
    day = usage.day.date()
    assert await _counter(tenant.business_id, UsageKind.TTS_REQUEST, day) == 1
    assert await _counter(other.business_id, UsageKind.TTS_REQUEST, day) == 0

    standard = await client.post(
        "/api/v1/agent/voice-preview", json={"voice": "standard"}, headers=_auth(tenant)
    )
    assert standard.status_code == 422
    assert standard.json()["error"]["code"] == "preview_unavailable"

    # At the limit: 429 for this business with no provider call; the other is unaffected.
    usage.set_daily(UsageKind.TTS_REQUEST, per_day=1000, per_business_per_day=1)
    limited = await client.post(
        "/api/v1/agent/voice-preview", json={"voice": "Kore"}, headers=_auth(tenant)
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "usage_limit_reached"
    assert len(tts.requests) == 1
    theirs = await client.post(
        "/api/v1/agent/voice-preview", json={"voice": "Kore"}, headers=_auth(other)
    )
    assert theirs.status_code == 200
    # A failing provider is a 503, never a fake success.
    usage.set_daily(UsageKind.TTS_REQUEST, per_day=1000, per_business_per_day=1000)
    tts.fail_next(TtsUnavailableError("bad request"))
    failed = await client.post(
        "/api/v1/agent/voice-preview", json={"voice": "Zephyr"}, headers=_auth(tenant)
    )
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "tts_unavailable"


async def test_without_a_gemini_key_the_voice_is_off(client, app, entitled_tenants, numbers):
    app.dependency_overrides[get_tts_client] = lambda: None
    tenant, _ = entitled_tenants

    voices = (await client.get("/api/v1/agent/voices", headers=_auth(tenant))).json()
    preview = await client.post(
        "/api/v1/agent/voice-preview", json={"voice": "Kore"}, headers=_auth(tenant)
    )
    _, to = await _associate(client, tenant, numbers)
    greeting = await _start(client, to, _call_sid())

    assert voices["tts_configured"] is False
    assert preview.status_code == 503 and preview.json()["error"]["code"] == "tts_not_configured"
    assert _played(greeting) == [] and greeting.said
