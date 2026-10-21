"""`GeminiClient`: the real LLM adapter (google-genai SDK, async).

Policy
- Model id, key and timeout come only from the constructor (settings).
- At most 2 attempts per `generate` (1 retry), and only for transient failures:
  timeouts, network errors, HTTP 408, 429 and 5xx. Anything else fails immediately.
- Every provider failure, and any empty / blocked / safety-stopped / truncated
  reply, becomes `LLMUnavailableError` with a short reason. Prompts, customer
  text and model output are never logged or put in the exception message.
- Issue #5: `UsageGuard` must wrap this client (quota, budget, rate limits), as
  `apps/api/CLAUDE.md` already says for Stripe. Retries here stay bounded.
"""

import asyncio
import logging
from typing import Any, get_args

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from integrations.llm.port import LLMUnavailableError, ModelMessage, ModelRequest
from schemas.agent_turn import Entities, Intent

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 0.5
_TEMPERATURE = 0.4
# Headroom for thinking tokens on 2.5 models plus a <=1000 char reply and entities.
_MAX_OUTPUT_TOKENS = 2048


def response_schema_for_gemini() -> dict[str, Any]:
    """The structured-output schema sent to Gemini.

    `AgentTurn.model_json_schema()` isn't usable as-is: `ToolCall.arguments` is a
    free-form `dict[str, Any]` (`additionalProperties: true`), which Gemini's schema
    subset can't express, and Pydantic's `$defs`/`anyOf: [..., null]` need
    flattening. While the tool registry is empty the model must not emit tool
    calls, so `tool_calls` is omitted here. The engine still validates the raw
    text against the full `AgentTurn` afterwards, so this schema only steers
    the model; it is never the source of truth. When tools land (Slice 6), add a
    `tool_calls` array whose arguments are a per-tool schema instead.
    """
    nullable_string: dict[str, Any] = {"type": "STRING", "nullable": True}
    return {
        "type": "OBJECT",
        "properties": {
            "intent": {"type": "STRING", "enum": list(get_args(Intent))},
            "response_text": {"type": "STRING"},
            "entities": {
                "type": "OBJECT",
                "properties": {name: dict(nullable_string) for name in Entities.model_fields},
            },
            "requires_human": {"type": "BOOLEAN"},
        },
        "required": ["intent", "response_text", "entities", "requires_human"],
        "propertyOrdering": ["intent", "response_text", "entities", "requires_human"],
    }


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.APIError):
        return exc.code in (408, 429)
    return isinstance(exc, TimeoutError | httpx.TransportError)


def _contents(messages: tuple[ModelMessage, ...]) -> list[types.Content]:
    return [types.Content(role=m.role, parts=[types.Part.from_text(text=m.text)]) for m in messages]


def _reply_text(response: Any) -> str:
    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        raise LLMUnavailableError("Gemini blocked the prompt.")
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        reason = getattr(candidates[0], "finish_reason", None)
        name = str(getattr(reason, "name", reason) or "STOP").removeprefix("FinishReason.")
        if name not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
            raise LLMUnavailableError(f"Gemini stopped early ({name}).")
    try:
        text = response.text
    except Exception as exc:  # SDK raises when there are no usable parts
        raise LLMUnavailableError("Gemini returned no text.") from exc
    if not text or not text.strip():
        raise LLMUnavailableError("Gemini returned an empty reply.")
    return text


class GeminiClient:
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
                raise LLMUnavailableError("Gemini API key is not configured.")
            self._sdk = genai.Client(
                api_key=self._api_key,
                # HttpOptions.timeout is in milliseconds. We retry ourselves.
                http_options=types.HttpOptions(
                    timeout=int(self._timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._sdk

    async def generate(self, request: ModelRequest) -> str:
        sdk = self._client()
        config = types.GenerateContentConfig(
            system_instruction=request.system_instruction,
            response_mime_type="application/json",
            response_schema=response_schema_for_gemini(),
            temperature=_TEMPERATURE,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
        contents = _contents(request.messages)
        last_reason = "unknown"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await asyncio.wait_for(
                    sdk.aio.models.generate_content(
                        model=self._model, contents=contents, config=config
                    ),
                    timeout=self._timeout_seconds + 1,
                )
                return _reply_text(response)
            except LLMUnavailableError:
                raise
            except Exception as exc:
                last_reason = type(exc).__name__
                transient = _is_transient(exc)
                logger.warning(
                    "gemini call failed (attempt %d/%d, %s, transient=%s)",
                    attempt,
                    MAX_ATTEMPTS,
                    last_reason,
                    transient,
                )
                if not transient or attempt == MAX_ATTEMPTS:
                    break
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
        raise LLMUnavailableError(f"Gemini call failed ({last_reason}).")
