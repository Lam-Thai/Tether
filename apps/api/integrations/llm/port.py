"""The LLM port: everything the conversation engine may ask of a model.

Plain dataclasses in, raw text out. The adapter never validates the model's
output itself: `services/conversation.py` parses it against `AgentTurn`, so
"the model said X" and "X is valid" stay separate steps. `GeminiClient` is
the real adapter; `FakeLLMClient` scripts replies turn by turn in tests.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel


class LLMUnavailableError(Exception):
    """The provider couldn't produce a reply: timeout, network, quota, safety block,
    or no API key configured. The engine answers with its safe fallback."""


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


class LLMClient(Protocol):
    async def generate(self, request: ModelRequest) -> str:
        """Return the model's raw reply text (expected to be JSON).

        Raises `LLMUnavailableError` for any provider-side failure. Retries are
        bounded inside the adapter; the caller never loops on this.
        """
        ...
