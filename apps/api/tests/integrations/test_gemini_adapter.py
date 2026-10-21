"""`GeminiClient` against a stub SDK client: no network, no key.

Covers the request mapping, the no-key path, token counts, and which failures
are flagged retryable (the adapter itself makes exactly one attempt).
"""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from integrations.llm.gemini import GeminiClient
from integrations.llm.port import LLMUnavailableError, ModelMessage, ModelRequest
from schemas.agent_turn import AgentTurn

OK_JSON = '{"intent":"FAQ","response_text":"Hello","entities":{},"requires_human":false}'


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


def reply(
    text: str | None = OK_JSON,
    finish: str = "STOP",
    block: str | None = None,
    usage: Any = None,
):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
        usage_metadata=usage,
    )


def client(sdk: Any, model: str = "model-from-settings") -> GeminiClient:
    return GeminiClient(api_key="k", model=model, timeout_seconds=5, sdk_client=sdk)


REQUEST = ModelRequest(
    system_instruction="SYSTEM TEXT",
    messages=(
        ModelMessage("model", "Hi, how can I help?"),
        ModelMessage("user", "What are your hours?"),
    ),
    response_schema=AgentTurn,
)


async def test_maps_request_to_sdk_call():
    sdk, models = stub_sdk(reply())
    assert (await client(sdk).generate(REQUEST)).text == OK_JSON

    (call,) = models.calls
    assert call["model"] == "model-from-settings"
    assert [(c.role, c.parts[0].text) for c in call["contents"]] == [
        ("model", "Hi, how can I help?"),
        ("user", "What are your hours?"),
    ]
    config = call["config"]
    assert config.system_instruction == "SYSTEM TEXT"
    assert config.response_mime_type == "application/json"
    assert config.max_output_tokens and config.max_output_tokens <= 4096
    budget = config.thinking_config.thinking_budget
    assert budget is not None and 0 <= budget < config.max_output_tokens
    assert config.temperature is not None


async def test_response_schema_is_gemini_compatible_and_omits_tool_calls():
    sdk, models = stub_sdk(reply())
    await client(sdk).generate(REQUEST)

    raw = models.calls[0]["config"].response_schema
    schema = types.Schema.model_validate(raw)  # the SDK's own schema type accepts it
    props = schema.properties or {}
    assert set(props) == {"intent", "response_text", "entities", "requires_human"}
    assert "UNKNOWN" in (props["intent"].enum or [])
    assert "customer_name" in (props["entities"].properties or {})
    assert "additionalProperties" not in str(raw)


async def test_no_api_key_raises_unavailable_without_calling_sdk():
    with pytest.raises(LLMUnavailableError):
        await GeminiClient(api_key=None, model="m", timeout_seconds=5).generate(REQUEST)


@pytest.mark.parametrize(
    "outcome",
    [
        reply(text=None),
        reply(text="   "),
        reply(finish="SAFETY"),
        reply(finish="MAX_TOKENS"),
        reply(text=None, block="SAFETY"),
    ],
)
async def test_empty_blocked_or_stopped_replies_are_unavailable(outcome: Any):
    sdk, models = stub_sdk(outcome)
    with pytest.raises(LLMUnavailableError):
        await client(sdk).generate(REQUEST)
    assert len(models.calls) == 1  # not retried


async def test_non_transient_error_is_not_retryable():
    sdk, models = stub_sdk(genai_errors.ClientError(400, {"error": {"message": "bad"}}))
    with pytest.raises(LLMUnavailableError) as raised:
        await client(sdk).generate(REQUEST)
    assert raised.value.retryable is False
    assert len(models.calls) == 1


@pytest.mark.parametrize(
    "transient",
    [
        genai_errors.ServerError(503, {"error": {"message": "overloaded"}}),
        genai_errors.ClientError(408, {"error": {"message": "request timeout"}}),
        genai_errors.ClientError(429, {"error": {"message": "quota"}}),
        TimeoutError(),
        httpx.ConnectError("boom"),
    ],
)
async def test_transient_errors_are_retryable_but_never_retried_here(transient: Exception):
    sdk, models = stub_sdk(transient, reply())
    with pytest.raises(LLMUnavailableError) as raised:
        await client(sdk).generate(REQUEST)
    assert raised.value.retryable is True
    assert len(models.calls) == 1  # the caller owns retries (retry_bounded + UsageGuard)
    assert "SYSTEM TEXT" not in str(raised.value)
    assert "hours" not in str(raised.value)


async def test_reports_token_counts_with_thinking_as_output():
    usage = SimpleNamespace(
        prompt_token_count=120, candidates_token_count=30, thoughts_token_count=12
    )
    sdk, _ = stub_sdk(reply(usage=usage))
    result = await client(sdk).generate(REQUEST)
    assert (result.tokens_in, result.tokens_out) == (120, 42)


async def test_a_truncated_reply_still_reports_the_tokens_it_spent():
    usage = SimpleNamespace(prompt_token_count=90, candidates_token_count=2048)
    sdk, _ = stub_sdk(reply(finish="MAX_TOKENS", usage=usage))
    with pytest.raises(LLMUnavailableError) as raised:
        await client(sdk).generate(REQUEST)
    assert (raised.value.tokens_in, raised.value.tokens_out) == (90, 2048)
    assert raised.value.retryable is False


async def test_missing_token_counts_are_zero():
    sdk, _ = stub_sdk(reply(usage=SimpleNamespace(prompt_token_count=None)))
    result = await client(sdk).generate(REQUEST)
    assert (result.tokens_in, result.tokens_out) == (0, 0)
