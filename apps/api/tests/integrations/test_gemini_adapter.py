"""`GeminiClient` against a stub SDK client: no network, no key.

Covers the request mapping, the no-key path, and the bounded-retry policy.
"""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from integrations.llm import gemini
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


def reply(text: str | None = OK_JSON, finish: str = "STOP", block: str | None = None):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
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


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini, "_RETRY_DELAY_SECONDS", 0)


async def test_maps_request_to_sdk_call():
    sdk, models = stub_sdk(reply())
    assert await client(sdk).generate(REQUEST) == OK_JSON

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


async def test_non_transient_error_is_not_retried():
    sdk, models = stub_sdk(genai_errors.ClientError(400, {"error": {"message": "bad"}}))
    with pytest.raises(LLMUnavailableError):
        await client(sdk).generate(REQUEST)
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
async def test_transient_error_is_retried_once_then_succeeds(transient: Exception):
    sdk, models = stub_sdk(transient, reply())
    assert await client(sdk).generate(REQUEST) == OK_JSON
    assert len(models.calls) == 2


async def test_retries_are_bounded_to_two_attempts():
    err = genai_errors.ServerError(500, {"error": {"message": "down"}})
    sdk, models = stub_sdk(err, err, err)
    with pytest.raises(LLMUnavailableError) as raised:
        await client(sdk).generate(REQUEST)
    assert len(models.calls) == 2
    assert "SYSTEM TEXT" not in str(raised.value)
    assert "hours" not in str(raised.value)
