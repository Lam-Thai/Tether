"""The LLM client dependency. Model ID, key and timeout come from settings only.

Tests override `get_llm_client` with `FakeLLMClient`; nothing else builds a
model client.
"""

from core.config import settings
from integrations.llm.gemini import GeminiClient
from integrations.llm.port import LLMClient

# One client per process (it holds an HTTP connection pool, not conversation state).
_client = GeminiClient(
    api_key=settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None,
    model=settings.gemini_model,
    timeout_seconds=settings.gemini_timeout_seconds,
)


def get_llm_client() -> LLMClient:
    return _client
