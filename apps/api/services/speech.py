"""`SpeechSynthesizer`: one line of agent text -> a short-lived audio token Twilio can `<Play>`.

    ref = await speech.synthesize(text, voice, business_id=..., interaction_id=..., cacheable=...)
    # `None`: use <Say> for this line.

Behind that small surface:

- The Gemini TTS call (`integrations/tts`, model id from settings), one attempt per try,
  `retry_bounded(TTS_RETRY)` around `UsageGuard.metered(TTS_REQUEST)`, so every attempt is
  reserved and counted, and the whole synthesis under one deadline (`tts_timeout_seconds`,
  further capped by what the caller has left of Twilio's webhook timeout).
- Conversion to Twilio's preferred `<Play>` format: 8 kHz G.711 mu-law WAV
  (`services/audio.py`), the smallest file and no transcoding on Twilio's side.
- Storage in `MediaStore` (`services/media.py`, Postgres): the URL token expires within
  minutes. A model reply's audio is kept only that long. FIXED lines (`cacheable=True`:
  the owner's greeting, holding phrases, reprompts; owner or platform text, never anything
  caller-derived) are cached per business under a key of format + model + voice + text,
  so a changed greeting or voice is simply a new key, and each call reuses the audio
  through a fresh short-lived token instead of spending another TTS request.
- Every failure (no key, a reached limit, a usage-store outage, a provider error, the
  deadline, a storage error) returns `None` and is logged as a fixed reason code: the call
  falls back to `<Say>` in the same turn and never drops. A reached limit makes no
  provider call and is never retried.

`preview` is the owner's "Preview voice": the same path (so previewing the saved greeting
also warms the call cache), returned as a browser-playable 16-bit WAV at the same 8 kHz,
i.e. exactly what a caller hears. It raises instead of falling back.

No FastAPI or SQLAlchemy imports.
"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from core.errors import AppError
from core.retry import retry_bounded
from integrations.tts.port import (
    TTS_RETRY,
    SpeechAudio,
    TtsClient,
    TtsUnavailableError,
    is_retryable,
)
from services.audio import mulaw_wav_to_pcm16_wav, pcm16_to_mulaw_wav
from services.usage import LimitExceededError, UsageGuard, UsageKind, UsageUnavailableError

logger = logging.getLogger("tether.speech")

STANDARD_VOICE = "standard"  # Twilio's own <Say> voice: no TTS at all
CALL_AUDIO_TYPE = "audio/wav"
# Part of every cache key: changing the stored format must not serve old files.
AUDIO_FORMAT = "mulaw-8k-wav-v1"
# Replies are capped at 1000 characters by `AgentTurn`; anything longer is not a call line.
MAX_TTS_CHARS = 1000
# The owner waits on the preview in the browser; a call line has Twilio's clock instead.
PREVIEW_TIMEOUT_SECONDS = 20.0

# Gemini's descriptions of its prebuilt voices; a configured voice not listed here is
# shown by name alone.
VOICE_DESCRIPTIONS: Mapping[str, str] = {
    "Kore": "Firm",
    "Puck": "Upbeat",
    "Charon": "Informative",
    "Aoede": "Breezy",
    "Fenrir": "Excitable",
    "Leda": "Youthful",
    "Orus": "Firm",
    "Zephyr": "Bright",
    "Callirrhoe": "Easy-going",
    "Autonoe": "Bright",
    "Enceladus": "Breathy",
    "Iapetus": "Clear",
    "Umbriel": "Easy-going",
    "Algieba": "Smooth",
    "Despina": "Smooth",
    "Erinome": "Clear",
    "Algenib": "Gravelly",
    "Rasalgethi": "Informative",
    "Laomedeia": "Upbeat",
    "Achernar": "Soft",
    "Alnilam": "Firm",
    "Schedar": "Even",
    "Gacrux": "Mature",
    "Pulcherrima": "Forward",
    "Achird": "Friendly",
    "Zubenelgenubi": "Casual",
    "Vindemiatrix": "Gentle",
    "Sadachbia": "Lively",
    "Sadaltager": "Knowledgeable",
    "Sulafat": "Warm",
}


class UnknownVoiceError(AppError):
    status_code = 422
    code = "unknown_voice"


class PreviewUnavailableError(AppError):
    """The standard voice is Twilio's own; there is nothing to synthesize."""

    status_code = 422
    code = "preview_unavailable"


class SpeechNotConfiguredError(AppError):
    status_code = 503
    code = "tts_not_configured"


class SpeechUnavailableError(AppError):
    status_code = 503
    code = "tts_unavailable"


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    description: str


class VoiceCatalog:
    """The voices an owner may pick (from settings) plus `standard`. An agent's stored
    voice is `None` until its owner picks one, which means the configured default; a stored
    voice later removed from the settings also falls back to the default."""

    def __init__(self, voices: Sequence[str], default: str) -> None:
        if not voices or default not in voices:
            raise ValueError("The default voice must be one of the voices.")
        self._voices = tuple(voices)
        self.default = default

    def options(self) -> list[Voice]:
        gemini = [Voice(v, v, VOICE_DESCRIPTIONS.get(v, "")) for v in self._voices]
        return [*gemini, Voice(STANDARD_VOICE, "Standard phone voice", "Twilio's built-in voice")]

    def is_valid(self, voice_id: str) -> bool:
        return voice_id == STANDARD_VOICE or voice_id in self._voices

    def effective(self, stored: str | None) -> str:
        """What the owner sees as selected: a voice name or `standard`."""
        return stored if stored is not None and self.is_valid(stored) else self.default

    def tts_voice(self, stored: str | None) -> str | None:
        """The voice to synthesize with, or `None` for Twilio's <Say>."""
        voice = self.effective(stored)
        return None if voice == STANDARD_VOICE else voice


class StoredAudio(Protocol):
    @property
    def content_type(self) -> str: ...
    @property
    def data(self) -> bytes: ...


class AudioStore(Protocol):
    """What this module needs from `services.media.MediaStore`."""

    async def put(
        self,
        business_id: uuid.UUID,
        data: bytes,
        content_type: str,
        *,
        ttl_seconds: int,
        cache_key: str | None = None,
    ) -> str: ...
    async def cached(self, business_id: uuid.UUID, cache_key: str) -> str | None: ...
    async def get(self, token: str) -> StoredAudio | None: ...


@dataclass(frozen=True)
class AudioRef:
    """A media token for `GET /api/v1/media/{token}`, valid for minutes."""

    token: str
    cached: bool  # served from the fixed-line cache: no TTS request was made


def cache_key(*, model: str, voice: str, text: str) -> str:
    return hashlib.sha256(f"{AUDIO_FORMAT}\n{model}\n{voice}\n{text}".encode()).hexdigest()


def _speakable(text: str) -> str:
    return " ".join(text.split())


class SpeechSynthesizer:
    def __init__(
        self,
        tts: TtsClient | None,
        store: AudioStore,
        usage: UsageGuard,
        *,
        catalog: VoiceCatalog,
        model: str,
        reply_ttl_seconds: int,
        cache_ttl_seconds: int,
        timeout_seconds: float,
    ) -> None:
        self._tts = tts
        self._store = store
        self._usage = usage
        self.catalog = catalog
        self._model = model
        self._reply_ttl = reply_ttl_seconds
        self._cache_ttl = cache_ttl_seconds
        self._timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        return self._tts is not None

    @property
    def timeout_seconds(self) -> float:
        """The most one call line's synthesis may take (callers budget around it)."""
        return self._timeout

    async def synthesize(
        self,
        text: str,
        voice: str,
        *,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID | None = None,
        cacheable: bool = False,
        deadline_seconds: float | None = None,
    ) -> AudioRef | None:
        """Audio for one call line, or `None` (use <Say>). Never raises (cancellation
        aside)."""
        if self._tts is None:
            return None
        budget = self._timeout if deadline_seconds is None else min(self._timeout, deadline_seconds)
        started = time.monotonic()
        outcome = "ok"
        try:
            if budget <= 0:
                outcome = "no_time"
                return None
            async with asyncio.timeout(budget):
                ref = await self._audio(text, voice, business_id, interaction_id, cacheable)
            if ref is None:
                outcome = "not_speakable"
            elif ref.cached:
                outcome = "cached"
            return ref
        except LimitExceededError:
            outcome = "limit_reached"
        except UsageUnavailableError:
            outcome = "usage_unavailable"
        except TtsUnavailableError as exc:
            outcome = "rate_limited" if exc.rate_limited else "provider_error"
        except TimeoutError:
            outcome = "timeout"
        except Exception as exc:
            # Storage or conversion: still a <Say> fallback, never a dropped call. The
            # type only: a database error's message can quote bound parameters.
            outcome = f"error:{type(exc).__name__}"
        finally:
            logger.info(
                "tts",
                extra={
                    "business_id": str(business_id),
                    "interaction_id": str(interaction_id) if interaction_id else None,
                    "outcome": outcome,
                    "tts_ms": int((time.monotonic() - started) * 1000),
                },
            )
        return None

    async def preview(self, text: str, voice: str, *, business_id: uuid.UUID) -> bytes:
        """The owner's preview of `text` (their saved greeting) in `voice`: a 16-bit PCM WAV
        at phone quality. Raises `UnknownVoiceError`, `PreviewUnavailableError`,
        `SpeechNotConfiguredError`, `LimitExceededError` (429, never retried),
        `UsageUnavailableError` or `SpeechUnavailableError`."""
        if not self.catalog.is_valid(voice):
            raise UnknownVoiceError("That voice isn't available.")
        if voice == STANDARD_VOICE:
            raise PreviewUnavailableError("The standard phone voice can't be previewed here.")
        if self._tts is None:
            raise SpeechNotConfiguredError("Voice generation isn't configured.")
        try:
            async with asyncio.timeout(PREVIEW_TIMEOUT_SECONDS):
                ref = await self._audio(text, voice, business_id, None, cacheable=True)
                stored = await self._store.get(ref.token) if ref is not None else None
        except (LimitExceededError, UsageUnavailableError):
            raise  # their own 429 / 503: the limit is the answer, not an outage
        except Exception as exc:
            # The provider, the deadline, storage or conversion: one 503, never a 500. The
            # type only: a database error's message can quote bound parameters.
            logger.info("tts preview failed", extra={"error_type": type(exc).__name__})
            raise SpeechUnavailableError("Voice preview isn't available right now.") from None
        if stored is None:
            raise SpeechUnavailableError("Voice preview isn't available right now.")
        return mulaw_wav_to_pcm16_wav(stored.data)

    async def _audio(
        self,
        text: str,
        voice: str,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID | None,
        cacheable: bool,
    ) -> AudioRef | None:
        line = _speakable(text)
        if not line or len(line) > MAX_TTS_CHARS:
            return None
        key = (
            cache_key(model=self._model, voice=voice, text=line)
            if cacheable and self._cache_ttl > 0
            else None
        )
        if key is not None:
            token = await self._store.cached(business_id, key)
            if token is not None:
                return AudioRef(token, cached=True)
        audio = await retry_bounded(
            lambda: self._attempt(line, voice, business_id, interaction_id),
            TTS_RETRY,
            is_retryable,
        )
        wav = pcm16_to_mulaw_wav(audio.pcm, audio.sample_rate)
        token = await self._store.put(
            business_id,
            wav,
            CALL_AUDIO_TYPE,
            ttl_seconds=self._cache_ttl if key is not None else self._reply_ttl,
            cache_key=key,
        )
        return AudioRef(token, cached=False)

    async def _attempt(
        self, text: str, voice: str, business_id: uuid.UUID, interaction_id: uuid.UUID | None
    ) -> SpeechAudio:
        assert self._tts is not None
        async with self._usage.metered(
            UsageKind.TTS_REQUEST, business_id=business_id, interaction_id=interaction_id
        ):
            return await self._tts.synthesize(text, voice)
