"""`GeminiTtsClient`: the real TTS adapter (Gemini TTS model through google-genai, async).

Policy
- Model id, key and timeout come only from the constructor (settings).
- One provider attempt per `synthesize`; the SDK's own retries are off. The caller retries
  with `retry_bounded` + `TTS_RETRY` around `UsageGuard.metered(TTS_REQUEST)`.
- Failures become `TtsUnavailableError` with a fixed reason. Only timeouts, transport
  errors and HTTP 408/429/5xx are `retryable`, and so is a reply that simply came back
  without audio (finish `OTHER`, no candidate, no or empty audio part): the TTS model
  does that intermittently and a second attempt usually works. A blocked prompt, a
  safety stop and non-PCM audio are not retryable.
- The text and the audio are never logged or put in an exception message.
"""

import asyncio
import logging
import re
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from integrations.tts.port import SpeechAudio, TtsUnavailableError

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 24000
_RATE = re.compile(r"rate=(\d+)", re.IGNORECASE)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.APIError):
        return exc.code in (408, 429)
    return isinstance(exc, TimeoutError | httpx.TransportError)


def _parse_pcm_mime(mime: str | None) -> int:
    """The sample rate of an `audio/L16;codec=pcm;rate=24000` mime (24000 if absent)."""
    lowered = (mime or "").lower()
    if lowered and not ("l16" in lowered or "pcm" in lowered):
        raise TtsUnavailableError("Gemini returned audio that is not PCM.")
    match = _RATE.search(lowered)
    rate = int(match.group(1)) if match else DEFAULT_SAMPLE_RATE
    if rate <= 0:
        raise TtsUnavailableError("Gemini returned an invalid sample rate.")
    return rate


def _speech(response: Any) -> SpeechAudio:
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        raise TtsUnavailableError("Gemini blocked the prompt.")
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise TtsUnavailableError("Gemini returned no audio.", retryable=True)
    reason = getattr(candidates[0], "finish_reason", None)
    name = str(getattr(reason, "name", reason) or "STOP").removeprefix("FinishReason.")
    if name == "OTHER":
        raise TtsUnavailableError("Gemini stopped early (OTHER).", retryable=True)
    if name not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise TtsUnavailableError(f"Gemini stopped early ({name}).")
    content = getattr(candidates[0], "content", None)
    chunks: list[bytes] = []
    rate: int | None = None
    for part in getattr(content, "parts", None) or []:
        inline = getattr(part, "inline_data", None)
        data = getattr(inline, "data", None)
        if not data:
            continue
        part_rate = _parse_pcm_mime(getattr(inline, "mime_type", None))
        if rate is not None and part_rate != rate:
            raise TtsUnavailableError("Gemini returned mixed sample rates.")
        rate = part_rate
        chunks.append(bytes(data))
    pcm = b"".join(chunks)
    if rate is None or not pcm:
        raise TtsUnavailableError("Gemini returned no audio.", retryable=True)
    return SpeechAudio(pcm=pcm, sample_rate=rate)


class GeminiTtsClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        sdk_client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._sdk = sdk_client

    def _client(self) -> Any:
        if self._sdk is None:
            if not self._api_key:
                raise TtsUnavailableError("Gemini API key is not configured.")
            self._sdk = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(
                    timeout=int(self._timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._sdk

    async def synthesize(self, text: str, voice: str) -> SpeechAudio:
        sdk = self._client()
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        )
        try:
            response = await asyncio.wait_for(
                sdk.aio.models.generate_content(model=self._model, contents=text, config=config),
                timeout=self._timeout_seconds + 1,
            )
        except Exception as exc:
            reason = type(exc).__name__
            transient = _is_transient(exc)
            logger.warning("gemini tts call failed (%s, transient=%s)", reason, transient)
            raise TtsUnavailableError(
                f"Gemini TTS call failed ({reason}).",
                retryable=transient,
                rate_limited=isinstance(exc, genai_errors.APIError) and exc.code == 429,
            ) from exc
        return _speech(response)
