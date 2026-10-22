"""`GeminiClient`: the real LLM adapter (google-genai SDK, async).

Policy
- Model id, key and timeout come only from the constructor (settings).
- One provider attempt per `generate`; the SDK's own retries are off. The
  caller retries with `core.retry.retry_bounded` + `LLM_RETRY` around
  `UsageGuard.metered`, so every attempt is reserved and counted.
- Every provider failure, and any empty / blocked / safety-stopped / truncated
  reply, becomes `LLMUnavailableError` with a short reason. Only transient
  failures (timeouts, network errors, HTTP 408, 429 and 5xx) are `retryable`.
  Prompts, customer text and model output are never logged or put in the
  exception message.
- Token counts come from the response's `usage_metadata` (thinking tokens
  count as output); missing counts are reported as 0.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any, get_args

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from integrations.llm.port import (
    LLMUnavailableError,
    ModelMessage,
    ModelReply,
    ModelRequest,
    ToolDeclaration,
)
from schemas.agent_turn import Entities, Intent

logger = logging.getLogger(__name__)

_TEMPERATURE = 0.4
# Thinking tokens count against max_output_tokens on 2.5 models, so cap thinking
# well below it to leave room for a <=1000 char reply and entities.
_MAX_OUTPUT_TOKENS = 2048
_THINKING_BUDGET_TOKENS = 1024


_GEMINI_TYPES = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
}


def response_schema_for_gemini(tools: Sequence[ToolDeclaration] = ()) -> dict[str, Any]:
    """The structured-output schema sent to Gemini.

    `AgentTurn.model_json_schema()` isn't usable as-is: `ToolCall.arguments` is a
    free-form `dict[str, Any]` (`additionalProperties: true`), which Gemini's schema
    subset can't express, and Pydantic's `$defs`/`anyOf: [..., null]` need
    flattening. The engine still validates the raw text against the full
    `AgentTurn` afterwards, and each tool's arguments against that tool's own
    args model, so this schema only steers the model; it is never the source of
    truth.

    Gemini 2.5 can't combine structured output with native function calling,
    so tool requests ride inside this JSON: with tools declared, `tool_calls`
    is an array of `{name, arguments}` where `name` is one of the declared
    tools and `arguments` has one property per parameter name across all of
    them (the union), each nullable, since one shape has to fit every tool.
    The executor treats a null argument as "not given". A name two tools share
    must have one type (a clash fails loudly); if they describe it differently
    the description is dropped (each tool's own is in the prompt's TOOLS list).
    Without tools, `tool_calls` is left out so the model can't emit one.
    """
    nullable_string: dict[str, Any] = {"type": "STRING", "nullable": True}
    properties: dict[str, Any] = {
        "intent": {"type": "STRING", "enum": list(get_args(Intent))},
        "sources": {"type": "ARRAY", "items": {"type": "STRING"}},
        "response_text": {"type": "STRING"},
        "entities": {
            "type": "OBJECT",
            "properties": {name: dict(nullable_string) for name in Entities.model_fields},
        },
        "requires_human": {"type": "BOOLEAN"},
    }
    if tools:
        properties["tool_calls"] = _tool_calls_schema(tools)
    # Decide on tools and sources before writing the reply.
    order = ["intent", "tool_calls", "sources", "response_text", "entities", "requires_human"]
    order = [name for name in order if name in properties]
    return {
        "type": "OBJECT",
        "properties": properties,
        "required": order,
        "propertyOrdering": order,
    }


def _tool_calls_schema(tools: Sequence[ToolDeclaration]) -> dict[str, Any]:
    arguments: dict[str, dict[str, Any]] = {}
    for tool in tools:
        for name, spec in dict(tool.parameters.get("properties") or {}).items():
            converted = {**_gemini_property(name, spec), "nullable": True}
            known = arguments.setdefault(name, converted)
            if known["type"] != converted["type"]:
                raise ValueError(f"Tool parameter {name!r} is declared with two different types.")
            first, other = known.get("description"), converted.get("description")
            if first and other and first != other:
                # Two tools describe it differently (search_faq's and get_services's
                # `query`): neither wording fits both, and each tool's own is in TOOLS.
                del known["description"]
    item: dict[str, Any] = {"name": {"type": "STRING", "enum": [tool.name for tool in tools]}}
    if arguments:  # Gemini rejects an OBJECT with no properties
        item["arguments"] = {"type": "OBJECT", "properties": arguments}
    return {
        "type": "ARRAY",
        "items": {"type": "OBJECT", "properties": item, "required": list(item)},
    }


def _gemini_property(name: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """One JSON-schema parameter (the subset `services/tools` emits) in Gemini's dialect."""
    kind = _GEMINI_TYPES.get(str(spec.get("type")))
    if kind is None:
        raise ValueError(f"Tool parameter {name!r} has an unsupported type.")
    converted: dict[str, Any] = {"type": kind}
    if spec.get("description"):
        converted["description"] = str(spec["description"])
    if kind == "STRING" and spec.get("enum"):
        converted["enum"] = [str(value) for value in spec["enum"]]
    if kind == "ARRAY":
        converted["items"] = _gemini_property(name, spec.get("items") or {})
    return converted


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.APIError):
        return exc.code in (408, 429)
    return isinstance(exc, TimeoutError | httpx.TransportError)


_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"


def _retry_delay(exc: Exception) -> float | None:
    """The `retryDelay` of a 429's `google.rpc.RetryInfo` detail ("39801s"), if any.
    Only that number is read; the error's message is never kept or logged."""
    body = getattr(exc, "details", None)
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(details, list):
        return None
    for detail in details:
        if isinstance(detail, dict) and detail.get("@type") == _RETRY_INFO:
            delay = str(detail.get("retryDelay", ""))
            try:
                return float(delay.removesuffix("s"))
            except ValueError:
                return None
    return None


def _token_counts(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage_metadata", None)

    def count(name: str) -> int:
        value = getattr(usage, name, None)
        return value if isinstance(value, int) and value > 0 else 0

    return (
        count("prompt_token_count"),
        count("candidates_token_count") + count("thoughts_token_count"),
    )


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

    async def generate(self, request: ModelRequest) -> ModelReply:
        sdk = self._client()
        config = types.GenerateContentConfig(
            system_instruction=request.system_instruction,
            response_mime_type="application/json",
            response_schema=response_schema_for_gemini(request.tools),
            temperature=_TEMPERATURE,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_budget=_THINKING_BUDGET_TOKENS),
        )
        try:
            response = await asyncio.wait_for(
                sdk.aio.models.generate_content(
                    model=self._model, contents=_contents(request.messages), config=config
                ),
                timeout=self._timeout_seconds + 1,
            )
        except Exception as exc:
            reason = type(exc).__name__
            transient = _is_transient(exc)
            logger.warning("gemini call failed (%s, transient=%s)", reason, transient)
            rate_limited = isinstance(exc, genai_errors.APIError) and exc.code == 429
            raise LLMUnavailableError(
                f"Gemini call failed ({reason}).",
                retryable=transient,
                rate_limited=rate_limited,
                retry_after_seconds=_retry_delay(exc) if rate_limited else None,
            ) from exc
        tokens_in, tokens_out = _token_counts(response)
        try:
            text = _reply_text(response)
        except LLMUnavailableError as exc:
            # A blocked or truncated reply still spent tokens: report them.
            exc.tokens_in, exc.tokens_out = tokens_in, tokens_out
            raise
        return ModelReply(text=text, tokens_in=tokens_in, tokens_out=tokens_out)
