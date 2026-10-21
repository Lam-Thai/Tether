"""The LLM port: everything the conversation engine may ask of a model.

Plain dataclasses in, raw text (plus token counts) out. The adapter never
validates the model's output itself: `services/conversation.py` parses it
against `AgentTurn`, so "the model said X" and "X is valid" stay separate
steps. `GeminiClient` is the real adapter; `FakeLLMClient` scripts replies
turn by turn in tests.

One `generate` is one provider attempt. Retrying is the caller's job, with
`core.retry.retry_bounded` and `LLM_RETRY`, wrapped around
`UsageGuard.metered` so every attempt is metered and counted on its own.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from core.retry import RetryPolicy

# At most 2 attempts per model call, retrying only `retryable` failures.
LLM_RETRY = RetryPolicy(attempts=2, backoff_seconds=0.5)


class LLMUnavailableError(Exception):
    """The provider couldn't produce a reply: timeout, network, quota, safety block,
    or no API key configured. The engine answers with its safe fallback.

    `retryable` is true only for transient failures (timeouts, transport errors,
    HTTP 408/429/5xx) where another attempt might succeed. Token counts are
    set when the provider consumed tokens anyway (a blocked or truncated reply)."""

    def __init__(
        self, message: str, *, retryable: bool = False, tokens_in: int = 0, tokens_out: int = 0
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, LLMUnavailableError) and exc.retryable


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["user", "model"]
    text: str


@dataclass(frozen=True)
class ToolDeclaration:
    """A backend operation the model may request (Slice 6 adds the first one)."""

    name: str
    description: str
    # JSON Schema object describing the arguments.
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRequest:
    system_instruction: str
    # Oldest first; the last entry is the customer's current utterance.
    messages: tuple[ModelMessage, ...]
    # The structured-output schema the reply must follow (`AgentTurn`).
    response_schema: type[BaseModel]
    tools: tuple[ToolDeclaration, ...] = ()


@dataclass(frozen=True)
class ModelReply:
    # The model's raw reply text (expected to be JSON).
    text: str
    # As reported by the provider; 0 when it doesn't say. Thinking counts as output.
    tokens_in: int = 0
    tokens_out: int = 0


class LLMClient(Protocol):
    async def generate(self, request: ModelRequest) -> ModelReply:
        """Make exactly one provider attempt and return the reply.

        Raises `LLMUnavailableError` for any provider-side failure, flagged
        `retryable` when another attempt could help. Never retries itself.
        """
        ...
