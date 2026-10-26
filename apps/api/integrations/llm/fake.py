"""In-memory fake of the LLM port, for tests.

Tests script the model turn by turn: each `generate` call pops the next
scripted reply (raw text, a dict serialized to JSON, or an exception to
raise). Every request is recorded so tests can assert on the assembled
prompt, and `len(requests)` is the number of provider calls made. Token
counts are a fixed, fake 100 in / 20 out per reply. Running out of script
fails the test loudly instead of inventing a reply.
"""

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from integrations.llm.port import ModelReply, ModelRequest

FAKE_TOKENS_IN = 100
FAKE_TOKENS_OUT = 20

Scripted = str | dict[str, Any] | Exception


@dataclass
class FakeLLMClient:
    script: deque[Scripted] = field(default_factory=deque)
    requests: list[ModelRequest] = field(default_factory=list)

    def queue(self, *replies: Scripted) -> None:
        self.script.extend(replies)

    async def generate(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("FakeLLMClient: no scripted reply left for this call.")
        reply = self.script.popleft()
        if isinstance(reply, Exception):
            raise reply
        text = json.dumps(reply) if isinstance(reply, dict) else reply
        return ModelReply(text=text, tokens_in=FAKE_TOKENS_IN, tokens_out=FAKE_TOKENS_OUT)


def agent_turn(
    response_text: str = "Happy to help.",
    *,
    intent: str = "UNKNOWN",
    requires_human: bool = False,
    entities: dict[str, Any] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """A valid `AgentTurn` payload for scripting `FakeLLMClient`."""
    return {
        "intent": intent,
        "response_text": response_text,
        "entities": entities or {},
        "requires_human": requires_human,
        "tool_calls": tool_calls or [],
        "sources": sources or [],
    }


def summary_reply(summary: str) -> dict[str, Any]:
    """A valid `SummaryReply` payload for scripting `FakeLLMClient`."""
    return {"summary": summary}
