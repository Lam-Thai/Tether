"""Plays one case's turns through the real `ConversationEngine` and records what happened.

What a scorer needs and the engine doesn't return:

- the model's own intent and cited sources. The engine reports `UNKNOWN` and
  shows a fixed line when its guard replaces a reply, so `play` keeps the raw
  replies of each turn (through `Recorder`) and parses the last valid one;
- which fallback, if any, the customer got (`FallbackKind`);
- the operation-log rows each turn wrote (tool name, status, machine summary);
- whether the provider rate-limited a call (HTTP 429), so the runner can back
  off and replay instead of scoring an outage as a wrong answer.

A `Sandbox` hands out one `CaseEnv` per case: a tenant, an engine bound to a
fresh store and the given LLM client, and a reader for the operation log.
Postgres in real runs (`evals/sandbox.py`), in-memory stores in tests.
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from core.tenant import Tenant
from evals.cases import CalendarMode, StoredInteraction
from integrations.llm.port import LLMClient, LLMUnavailableError, ModelReply, ModelRequest
from schemas.agent_turn import AgentTurn
from services.agent import require_business
from services.conversation import (
    INVALID_OUTPUT_FALLBACK,
    PROVIDER_FALLBACK,
    ConversationEngine,
    availability_fallback,
    booking_fallback,
    no_info_fallback,
    quota_fallback,
)
from services.summarizer import Summary

CHANNEL = "test_chat"

# `template`: a summary the model could not produce validly (the `summary` suite).
FallbackKind = Literal[
    "provider", "invalid_output", "no_info", "quota", "availability", "booking", "template"
]


class ProviderUnavailableError(Exception):
    """The model never produced a summary (no reply, not rate-limited): a case error."""


@dataclass(frozen=True)
class Operation:
    tool_name: str
    status: str
    # The operation log's machine summary (ids, counts, reason codes), never free text.
    result: Mapping[str, Any]


@dataclass(frozen=True)
class PlayedTurn:
    customer: str
    reply: str
    # What the engine recorded: the shown reply's intent, `UNKNOWN` on a fallback.
    intent: str
    # The model's intent in its last valid reply this turn (None if none was valid).
    model_intent: str | None
    # Sources cited by the shown reply (empty on a fallback).
    sources: tuple[str, ...]
    fallback: FallbackKind | None
    operations: tuple[Operation, ...]
    model_calls: int
    rate_limited: bool
    # The longest retry delay the provider asked for in this turn, if it gave one.
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class Playback:
    turns: tuple[PlayedTurn, ...]

    @property
    def operations(self) -> tuple[Operation, ...]:
        return tuple(op for turn in self.turns for op in turn.operations)

    @property
    def model_calls(self) -> int:
        return sum(turn.model_calls for turn in self.turns)

    @property
    def usage_limited(self) -> bool:
        """The eval budget or a usage limit stopped a turn (no model call was made)."""
        return any(turn.fallback == "quota" for turn in self.turns)

    @property
    def rate_limited_out(self) -> bool:
        """A turn fell back because the provider kept rate-limiting it."""
        return any(t.fallback == "provider" and t.rate_limited for t in self.turns)

    @property
    def retry_after_seconds(self) -> float | None:
        delays = [t.retry_after_seconds for t in self.turns if t.retry_after_seconds is not None]
        return max(delays) if delays else None


@dataclass
class Recorder:
    """An `LLMClient` that passes calls through and keeps the raw replies of one case."""

    inner: LLMClient
    replies: list[str] = field(default_factory=list)
    calls: int = 0
    rate_limited: bool = False
    retry_after_seconds: float | None = None

    async def generate(self, request: ModelRequest) -> ModelReply:
        self.calls += 1
        try:
            reply = await self.inner.generate(request)
        except LLMUnavailableError as exc:
            if exc.rate_limited:
                self.rate_limited = True
                if exc.retry_after_seconds is not None:
                    self.retry_after_seconds = max(
                        exc.retry_after_seconds, self.retry_after_seconds or 0.0
                    )
            raise
        self.replies.append(reply.text)
        return reply


@dataclass(frozen=True)
class CaseEnv:
    tenant: Tenant
    engine: ConversationEngine
    # The operation-log rows of one interaction, oldest first.
    operations: Callable[[uuid.UUID], Awaitable[Sequence[Operation]]]
    # Ends the unit of work, as the router's commit does after each request.
    commit: Callable[[], Awaitable[None]]


class Sandbox(Protocol):
    def case(
        self, llm: LLMClient, calendar: CalendarMode = "busy"
    ) -> AbstractAsyncContextManager[CaseEnv]:
        """A fresh engine for one case, calling the model through `llm`, with the case's
        scripted calendar."""
        ...

    async def summarize(self, llm: LLMClient, stored: StoredInteraction) -> Summary | None:
        """Seed `stored` as a finished interaction of the sandbox tenant and run the real
        `Summarizer` over it with `llm` (the `summary` suite)."""
        ...


def _parse(raw: str) -> AgentTurn | None:
    try:
        return AgentTurn.model_validate_json(raw)
    except ValidationError:
        return None


def _fallback_kind(env: CaseEnv, reply: str, fell_back: bool) -> FallbackKind | None:
    if not fell_back:
        return None
    business = require_business(env.tenant)
    kinds: dict[str, FallbackKind] = {
        PROVIDER_FALLBACK: "provider",
        INVALID_OUTPUT_FALLBACK: "invalid_output",
        no_info_fallback(business): "no_info",
        quota_fallback(business): "quota",
        availability_fallback(business): "availability",
        booking_fallback(business): "booking",
    }
    # Unknown text can't happen today; treat it as an outage rather than an answer.
    return kinds.get(reply, "provider")


async def play(env: CaseEnv, recorder: Recorder, utterances: Sequence[str]) -> Playback:
    """One new conversation: every utterance in order, then the interaction is ended.

    Stops early after a turn that hit a usage limit or was rate-limited out:
    later turns would only repeat the same fallback.
    """
    turns: list[PlayedTurn] = []
    interaction_id: uuid.UUID | None = None
    seen_ops = 0
    for utterance in utterances:
        replies_before, calls_before = len(recorder.replies), recorder.calls
        recorder.rate_limited, recorder.retry_after_seconds = False, None
        result = await env.engine.handle_turn(env.tenant, interaction_id, utterance, CHANNEL)
        await env.commit()  # one turn is one request, as in the chat API
        interaction_id = result.interaction_id
        ops = await env.operations(interaction_id)
        parsed = [t for t in map(_parse, recorder.replies[replies_before:]) if t is not None]
        shown = parsed[-1] if parsed and not result.fallback else None
        turn = PlayedTurn(
            customer=utterance,
            reply=result.reply_text,
            intent=result.intent,
            model_intent=parsed[-1].intent if parsed else None,
            sources=tuple(shown.sources) if shown else (),
            fallback=_fallback_kind(env, result.reply_text, result.fallback),
            operations=tuple(ops[seen_ops:]),
            model_calls=recorder.calls - calls_before,
            rate_limited=recorder.rate_limited,
            retry_after_seconds=recorder.retry_after_seconds,
        )
        seen_ops = len(ops)
        turns.append(turn)
        if turn.fallback == "quota" or (turn.fallback == "provider" and turn.rate_limited):
            break
    if interaction_id is not None:
        await env.engine.end(env.tenant, interaction_id, CHANNEL)
        await env.commit()
    return Playback(turns=tuple(turns))


async def play_summary(sandbox: Sandbox, recorder: Recorder, stored: StoredInteraction) -> Playback:
    """One `Summarizer` call over a seeded interaction, as a one-turn `Playback`: the
    "reply" is the stored summary. A template means the model did not deliver: `quota`
    when no model call was made (the budget), `provider` when it was rate-limited (the
    runner backs off and replays), `template` when the model replied but every reply was
    invalid. A template with model calls but no reply and no rate limit (an outage, a
    timeout, a blocked reply) raises `ProviderUnavailableError`: the model never wrote a
    summary, so the case is an error, never a template the scorers would pass."""
    summary = await sandbox.summarize(recorder, stored)
    if summary is None:
        raise RuntimeError("the seeded interaction was not summarized")
    fallback: FallbackKind | None = None
    if summary.source == "template":
        if recorder.calls == 0:
            fallback = "quota"
        elif recorder.rate_limited:
            fallback = "provider"
        elif not recorder.replies:
            raise ProviderUnavailableError("the model produced no summary")
        else:
            fallback = "template"
    turn = PlayedTurn(
        customer=f"[stored interaction: {len(stored.messages)} messages]",
        reply=summary.text,
        intent=stored.intent or "UNKNOWN",
        model_intent=None,
        sources=(),
        fallback=fallback,
        operations=(),
        model_calls=recorder.calls,
        rate_limited=recorder.rate_limited,
        retry_after_seconds=recorder.retry_after_seconds,
    )
    return Playback(turns=(turn,))
