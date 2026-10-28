"""Dependencies for the agent's voice (slice 15): the TTS client, the voice catalog and
`SpeechSynthesizer`. Model id, key, voices and limits come from settings only.

Without `GEMINI_API_KEY` there is no TTS client: calls keep Twilio's <Say> and the preview
answers 503 `tts_not_configured`. Tests override `get_tts_client` with `FakeTtsClient`
(and `get_media_store` / `get_usage_guard` as usual).
"""

from fastapi import Depends

from core.config import settings
from core.media import get_media_store
from core.usage import get_usage_guard
from integrations.tts.gemini import GeminiTtsClient
from integrations.tts.port import TtsClient
from services.media import MediaStore
from services.speech import SpeechSynthesizer, VoiceCatalog
from services.usage import UsageGuard

# One client per process (it holds an HTTP connection pool, not conversation state).
_client: TtsClient | None = (
    GeminiTtsClient(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_tts_model,
        timeout_seconds=settings.tts_timeout_seconds,
    )
    if settings.gemini_api_key and settings.gemini_api_key.get_secret_value()
    else None
)
_catalog = VoiceCatalog(settings.tts_voices, settings.tts_default_voice)


def get_tts_client() -> TtsClient | None:
    return _client


def get_voice_catalog() -> VoiceCatalog:
    return _catalog


def get_speech_synthesizer(
    tts: TtsClient | None = Depends(get_tts_client),
    media: MediaStore = Depends(get_media_store),
    usage: UsageGuard = Depends(get_usage_guard),
    catalog: VoiceCatalog = Depends(get_voice_catalog),
) -> SpeechSynthesizer:
    return SpeechSynthesizer(
        tts,
        media,
        usage,
        catalog=catalog,
        model=settings.gemini_tts_model,
        reply_ttl_seconds=settings.media_token_ttl_seconds,
        cache_ttl_seconds=settings.tts_cache_ttl_seconds,
        timeout_seconds=settings.tts_timeout_seconds,
    )
