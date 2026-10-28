"""`GeminiTtsClient` against a stub SDK client: no network, no key."""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors

from integrations.tts import FakeTtsClient, GeminiTtsClient, TtsUnavailableError

PCM = b"\x01\x00\x02\x00\x03\x00"


class StubModels:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def stub_sdk(*outcomes: Any) -> tuple[SimpleNamespace, StubModels]:
    models = StubModels(*outcomes)
    return SimpleNamespace(aio=SimpleNamespace(models=models)), models


def audio(data: bytes | None = PCM, mime: str | None = "audio/L16;codec=pcm;rate=24000"):
    return SimpleNamespace(inline_data=SimpleNamespace(data=data, mime_type=mime))


def reply(
    parts: list[Any] | None = None,
    finish: str = "STOP",
    block: str | None = None,
    candidates: bool = True,
):
    if parts is None:
        parts = [audio()]
    return SimpleNamespace(
        candidates=(
            [
                SimpleNamespace(
                    finish_reason=SimpleNamespace(name=finish),
                    content=SimpleNamespace(parts=parts),
                )
            ]
            if candidates
            else []
        ),
        prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
    )


def client(sdk: Any) -> GeminiTtsClient:
    return GeminiTtsClient(
        api_key="k", model="tts-from-settings", timeout_seconds=5, sdk_client=sdk
    )


async def test_success_parses_bytes_and_rate_and_maps_the_request():
    sdk, models = stub_sdk(reply([audio(mime="audio/L16;codec=pcm;rate=16000")]))
    result = await client(sdk).synthesize("Hello there", "Kore")
    assert (result.pcm, result.sample_rate) == (PCM, 16000)

    (call,) = models.calls
    assert call["model"] == "tts-from-settings"
    assert call["contents"] == "Hello there"
    config = call["config"]
    assert config.response_modalities == ["AUDIO"]
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"


async def test_a_missing_rate_defaults_to_24000_and_chunks_are_joined():
    sdk, _ = stub_sdk(reply([audio(b"ab", "audio/L16"), audio(b"cd", None)]))
    result = await client(sdk).synthesize("x", "Kore")
    assert (result.pcm, result.sample_rate) == (b"abcd", 24000)


async def test_no_api_key_raises_without_calling_the_sdk():
    with pytest.raises(TtsUnavailableError) as raised:
        await GeminiTtsClient(api_key=None, model="m", timeout_seconds=5).synthesize("x", "Kore")
    assert raised.value.retryable is False


@pytest.mark.parametrize(
    "outcome",
    [
        reply(block="SAFETY", candidates=False),
        reply(finish="SAFETY"),
        reply(parts=[audio(mime="audio/mpeg")]),
        reply(parts=[audio(mime="audio/L16;rate=0")]),
    ],
)
async def test_blocked_safety_stopped_or_non_pcm_audio_is_not_retryable(outcome: Any):
    sdk, models = stub_sdk(outcome)
    with pytest.raises(TtsUnavailableError) as raised:
        await client(sdk).synthesize("secret words", "Kore")
    assert raised.value.retryable is False
    assert "secret" not in str(raised.value)
    assert len(models.calls) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        reply(finish="OTHER"),
        reply(candidates=False),
        reply(parts=[]),
        reply(parts=[SimpleNamespace(inline_data=None)]),
        reply(parts=[audio(b"")]),
    ],
)
async def test_a_reply_that_came_back_without_audio_is_retryable(outcome: Any):
    # The TTS model intermittently answers with no audio; a second attempt usually works.
    sdk, models = stub_sdk(outcome)
    with pytest.raises(TtsUnavailableError) as raised:
        await client(sdk).synthesize("secret words", "Kore")
    assert raised.value.retryable is True
    assert "secret" not in str(raised.value)
    assert len(models.calls) == 1


async def test_a_400_is_not_retryable():
    sdk, models = stub_sdk(genai_errors.ClientError(400, {"error": {"message": "bad"}}))
    with pytest.raises(TtsUnavailableError) as raised:
        await client(sdk).synthesize("x", "Kore")
    assert (raised.value.retryable, raised.value.rate_limited) == (False, False)
    assert len(models.calls) == 1


@pytest.mark.parametrize(
    "transient",
    [
        genai_errors.ServerError(503, {"error": {"message": "overloaded"}}),
        genai_errors.ClientError(408, {"error": {"message": "timeout"}}),
        genai_errors.ClientError(429, {"error": {"message": "quota"}}),
        TimeoutError(),
        httpx.ConnectError("boom"),
    ],
)
async def test_transient_errors_are_retryable_but_never_retried_here(transient: Exception):
    sdk, models = stub_sdk(transient, reply())
    with pytest.raises(TtsUnavailableError) as raised:
        await client(sdk).synthesize("hello", "Kore")
    assert raised.value.retryable is True
    assert len(models.calls) == 1
    assert "hello" not in str(raised.value)
    is_429 = isinstance(transient, genai_errors.APIError) and transient.code == 429
    assert raised.value.rate_limited is is_429


async def test_the_fake_is_deterministic_records_requests_and_fails_on_script():
    fake = FakeTtsClient()
    short = await fake.synthesize("Hi", "Kore")
    again = await fake.synthesize("Hi", "Kore")
    long = await fake.synthesize("Hello there, how can I help?", "Kore")
    assert short == again and short.pcm != long.pcm
    assert len(long.pcm) > len(short.pcm) and short.sample_rate == 24000
    assert [r[0] for r in fake.requests] == ["Hi", "Hi", "Hello there, how can I help?"]

    fake.fail_next(TtsUnavailableError("down", retryable=True), times=2)
    for _ in range(2):
        with pytest.raises(TtsUnavailableError):
            await fake.synthesize("x", "Kore")
    await fake.synthesize("x", "Kore")
    assert len(fake.requests) == 6
