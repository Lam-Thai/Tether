"""Unit tests for the conversation engine: in-memory store + scripted fake model, no DB."""

import asyncio
import time
import uuid
from datetime import UTC, datetime

import pytest

from core.tenant import Tenant
from integrations.llm.fake import FakeLLMClient, agent_turn
from integrations.llm.port import LLMUnavailableError, ToolDeclaration
from models.agent import Agent
from models.business import Business
from models.interaction import Interaction, InteractionMessage
from models.user import User
from services import agent as agent_service
from services.conversation import (
    INVALID_OUTPUT_FALLBACK,
    PROVIDER_FALLBACK,
    ConversationEngine,
    InteractionEndedError,
    InteractionNotFoundError,
)
from services.tools import ToolRegistry


class MemoryStore:
    """Same contract as `SqlConversationStore`, scoped by business id like the real one."""

    def __init__(self) -> None:
        self.agents: dict[uuid.UUID, Agent] = {}
        self.interactions: dict[uuid.UUID, Interaction] = {}
        self.messages: list[InteractionMessage] = []
        self.calls: list[str] = []

    async def get_or_create(self, business_id, *, name, tone, greeting):
        if business_id not in self.agents:
            self.agents[business_id] = Agent(
                id=uuid.uuid4(),
                business_id=business_id,
                name=name,
                tone=tone,
                greeting=greeting,
                custom_instructions="",
                active=True,
            )
        return self.agents[business_id]

    async def save(self, agent):
        return agent

    async def create_interaction(self, business_id, channel):
        interaction = Interaction(
            id=uuid.uuid4(),
            business_id=business_id,
            channel=channel,
            status="active",
            state={},
            started_at=_NOW,
            ended_at=None,
        )
        self.interactions[interaction.id] = interaction
        return interaction

    async def get_interaction(self, business_id, interaction_id, channel):
        found = self.interactions.get(interaction_id)
        if found and found.business_id == business_id and found.channel == channel:
            return found
        return None

    lock_interaction = get_interaction

    async def append_message(self, interaction, speaker, message_text):
        number = 1 + sum(1 for m in self.messages if m.interaction_id == interaction.id)
        message = InteractionMessage(
            id=uuid.uuid4(),
            interaction_id=interaction.id,
            business_id=interaction.business_id,
            speaker=speaker,
            message_text=message_text,
            sequence_number=number,
            created_at=_NOW,
        )
        self.messages.append(message)
        return message

    async def list_messages(self, business_id, interaction_id, limit=None):
        rows = [
            m
            for m in self.messages
            if m.business_id == business_id and m.interaction_id == interaction_id
        ]
        return rows[-limit:] if limit else rows

    async def save_interaction(self, interaction):
        return interaction

    async def checkpoint(self):
        self.calls.append("checkpoint")


_NOW = datetime(2026, 10, 20, tzinfo=UTC)


def _tenant(name: str = "Alpha Salon") -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name=name,
        industry="Salon",
        address="1 Main St",
        timezone="America/New_York",
        phone=None,
        contact_email=None,
    )
    return Tenant(user=user, business=business)


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def _engine(
    store, llm, *, history_limit: int = 20, tools: ToolRegistry | None = None, timeout: float = 5
):
    return ConversationEngine(store, llm, tools or ToolRegistry(), history_limit, timeout)


def _texts(request) -> list[tuple[str, str]]:
    return [(m.role, m.text) for m in request.messages]


async def test_new_chat_persists_the_owners_greeting_as_message_one(store, llm):
    tenant = _tenant()
    llm.queue(agent_turn("We open at nine.", intent="BUSINESS_HOURS"))

    result = await _engine(store, llm).handle_turn(tenant, None, "When do you open?", "test_chat")

    assert [(m.sequence_number, m.speaker) for m in result.messages] == [
        (1, "agent"),
        (2, "customer"),
        (3, "agent"),
    ]
    assert (
        result.messages[0].text
        == "Hi, thanks for contacting Alpha Salon! How can I help you today?"
    )
    assert result.reply_text == "We open at nine."
    assert result.intent == "BUSINESS_HOURS"
    assert result.fallback is False and result.ended is False and result.status == "active"
    # Gemini saw the greeting as the opening model message, then the question.
    assert _texts(llm.requests[0]) == [
        ("model", result.messages[0].text),
        ("user", "When do you open?"),
    ]


async def test_invalid_json_is_retried_once_and_the_valid_reply_is_used(store, llm):
    llm.queue("not json at all", agent_turn("Sure thing."))

    result = await _engine(store, llm).handle_turn(_tenant(), None, "hi", "test_chat")

    assert result.reply_text == "Sure thing."
    assert result.fallback is False
    assert len(llm.requests) == 2
    assert llm.requests[0] == llm.requests[1]
    interaction = next(iter(store.interactions.values()))
    assert [f["kind"] for f in interaction.state["failures"]] == ["invalid_output"]
    assert "clarification_attempts" not in interaction.state


async def test_invalid_twice_falls_back_and_records_the_failures(store, llm):
    secret = "RAW-MODEL-TEXT-DO-NOT-STORE"
    llm.queue(secret, {"intent": "NOPE", "response_text": secret})

    result = await _engine(store, llm).handle_turn(_tenant(), None, "hi", "test_chat")

    assert len(llm.requests) == 2
    assert result.fallback is True
    assert result.reply_text == INVALID_OUTPUT_FALLBACK
    assert result.intent == "UNKNOWN"
    interaction = next(iter(store.interactions.values()))
    assert [f["kind"] for f in interaction.state["failures"]] == ["invalid_output"] * 2
    assert interaction.state["clarification_attempts"] == 1
    assert secret not in str(interaction.state)
    # The fallback is what the transcript shows as the agent's reply.
    assert store.messages[-1].message_text == INVALID_OUTPUT_FALLBACK


async def test_provider_unavailable_falls_back_without_an_engine_retry(store, llm):
    llm.queue(LLMUnavailableError("timeout"))

    result = await _engine(store, llm).handle_turn(_tenant(), None, "hi", "test_chat")

    assert len(llm.requests) == 1
    assert result.fallback is True
    assert result.reply_text == PROVIDER_FALLBACK
    interaction = next(iter(store.interactions.values()))
    assert [f["kind"] for f in interaction.state["failures"]] == ["provider_unavailable"]
    assert "clarification_attempts" not in interaction.state


async def test_unregistered_tool_call_is_invalid_and_never_executed(store, llm):
    bad = agent_turn("Booked!", tool_calls=[{"name": "book_appointment", "arguments": {}}])
    llm.queue(bad, bad)

    result = await _engine(store, llm).handle_turn(_tenant(), None, "book me", "test_chat")

    assert result.fallback is True
    assert result.reply_text == INVALID_OUTPUT_FALLBACK
    assert "Booked!" not in {m.message_text for m in store.messages}
    interaction = next(iter(store.interactions.values()))
    assert {f["detail"] for f in interaction.state["failures"]} == {"unregistered_tool"}


async def test_a_registered_tool_name_passes_validation(store, llm):
    tools = ToolRegistry.of([ToolDeclaration(name="search_faq", description="Search FAQs.")])
    llm.queue(agent_turn("Let me check.", tool_calls=[{"name": "search_faq", "arguments": {}}]))

    result = await _engine(store, llm, tools=tools).handle_turn(_tenant(), None, "q", "test_chat")

    assert result.fallback is False
    assert llm.requests[0].tools == tools.declarations()


async def test_persona_changes_apply_to_the_next_turn_without_restart(store, llm):
    tenant = _tenant()
    engine = _engine(store, llm)
    llm.queue(agent_turn("One."), agent_turn("Two."))

    first = await engine.handle_turn(tenant, None, "hello", "test_chat")
    await agent_service.update_agent(
        store,
        tenant,
        name="Morgan",
        tone="concise",
        greeting="Yo.",
        custom_instructions="Say ahoy.",
    )
    await engine.handle_turn(tenant, first.interaction_id, "again", "test_chat")

    before, after = (r.system_instruction for r in llm.requests)
    assert "Riley" in before and "Morgan" not in before
    assert "Morgan" in after and "concise" in after and "Say ahoy." in after
    assert "Riley" not in after


async def test_history_limit_keeps_the_most_recent_messages_in_order(store, llm):
    tenant = _tenant()
    engine = _engine(store, llm, history_limit=3)
    llm.queue(*[agent_turn(f"reply {i}") for i in range(3)])

    first = await engine.handle_turn(tenant, None, "q0", "test_chat")
    await engine.handle_turn(tenant, first.interaction_id, "q1", "test_chat")
    await engine.handle_turn(tenant, first.interaction_id, "q2", "test_chat")

    # Stored before the third turn: greeting, q0, reply 0, q1, reply 1 (oldest dropped).
    assert _texts(llm.requests[2]) == [
        ("model", "reply 0"),
        ("user", "q1"),
        ("model", "reply 1"),
        ("user", "q2"),
    ]


async def test_entities_are_merged_across_turns(store, llm):
    tenant = _tenant()
    engine = _engine(store, llm)
    llm.queue(
        agent_turn("a", entities={"service_name": "Haircut"}),
        agent_turn("b", entities={"customer_name": "Sam"}, requires_human=True),
    )

    first = await engine.handle_turn(tenant, None, "x", "test_chat")
    await engine.handle_turn(tenant, first.interaction_id, "y", "test_chat")

    state = store.interactions[first.interaction_id].state
    assert state["entities"] == {"service_name": "Haircut", "customer_name": "Sam"}
    assert state["requires_human"] is True


async def test_blank_entity_does_not_overwrite_earlier_value(store, llm):
    tenant = _tenant()
    engine = _engine(store, llm)
    llm.queue(
        agent_turn("a", entities={"customer_name": "Sam"}),
        agent_turn("b", entities={"customer_name": "   "}),
    )

    first = await engine.handle_turn(tenant, None, "x", "test_chat")
    await engine.handle_turn(tenant, first.interaction_id, "y", "test_chat")

    assert store.interactions[first.interaction_id].state["entities"] == {"customer_name": "Sam"}


async def test_ended_interaction_is_409_and_end_is_idempotent(store, llm):
    tenant = _tenant()
    engine = _engine(store, llm)
    llm.queue(agent_turn("hi"))
    first = await engine.handle_turn(tenant, None, "x", "test_chat")

    ended = await engine.end(tenant, first.interaction_id, "test_chat")
    again = await engine.end(tenant, first.interaction_id, "test_chat")

    assert ended.ended and ended.status == "ended" and ended.ended_at is not None
    assert again.ended_at == ended.ended_at
    assert len(again.messages) == 3
    with pytest.raises(InteractionEndedError):
        await engine.handle_turn(tenant, first.interaction_id, "more", "test_chat")
    assert len(llm.requests) == 1  # the rejected send never reached the model


async def test_another_tenants_or_unknown_interaction_is_404(store, llm):
    owner, other = _tenant(), _tenant("Beta Barbers")
    engine = _engine(store, llm)
    llm.queue(agent_turn("hi"))
    first = await engine.handle_turn(owner, None, "x", "test_chat")

    for call in (
        engine.handle_turn(other, first.interaction_id, "x", "test_chat"),
        engine.transcript(other, first.interaction_id, "test_chat"),
        engine.end(other, first.interaction_id, "test_chat"),
        engine.transcript(owner, uuid.uuid4(), "test_chat"),
    ):
        with pytest.raises(InteractionNotFoundError):
            await call


async def test_interleaved_chats_keep_separate_histories(store, llm):
    tenant = _tenant()
    engine = _engine(store, llm)
    llm.queue(*[agent_turn(f"r{i}") for i in range(4)])

    a = await engine.handle_turn(tenant, None, "a1", "test_chat")
    b = await engine.handle_turn(tenant, None, "b1", "test_chat")
    await engine.handle_turn(tenant, a.interaction_id, "a2", "test_chat")
    await engine.handle_turn(tenant, b.interaction_id, "b2", "test_chat")

    a2, b2 = _texts(llm.requests[2]), _texts(llm.requests[3])
    assert [t for _, t in a2][1:] == ["a1", "r0", "a2"]
    assert [t for _, t in b2][1:] == ["b1", "r1", "b2"]
    assert a.interaction_id != b.interaction_id


class _SlowLLM(FakeLLMClient):
    """Sleeps (async) before answering, to trip the per-turn deadline."""

    async def generate(self, request):
        await asyncio.sleep(1)
        return await super().generate(request)


class _BlockingInvalidLLM(FakeLLMClient):
    """Burns the whole deadline without yielding, then returns invalid output."""

    async def generate(self, request):
        self.requests.append(request)
        time.sleep(0.08)
        return "not json"


class _OrderedLLM(FakeLLMClient):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def generate(self, request):
        self._calls.append("generate")
        return await super().generate(request)


async def test_turn_deadline_gives_the_provider_fallback_and_records_turn_timeout(store):
    llm = _SlowLLM()
    llm.queue(agent_turn("never seen"))

    result = await _engine(store, llm, timeout=0.05).handle_turn(_tenant(), None, "hi", "test_chat")

    assert result.fallback is True
    assert result.reply_text == PROVIDER_FALLBACK
    state = next(iter(store.interactions.values())).state
    assert [(f["kind"], f["detail"]) for f in state["failures"]] == [
        ("provider_unavailable", "turn_timeout")
    ]
    assert "clarification_attempts" not in state


async def test_no_invalid_output_retry_once_the_deadline_has_passed(store):
    llm = _BlockingInvalidLLM()

    result = await _engine(store, llm, timeout=0.05).handle_turn(_tenant(), None, "hi", "test_chat")

    assert len(llm.requests) == 1
    assert result.reply_text == PROVIDER_FALLBACK
    kinds = [
        (f["kind"], f["detail"]) for f in next(iter(store.interactions.values())).state["failures"]
    ]
    assert kinds == [("invalid_output", "json_invalid"), ("provider_unavailable", "turn_timeout")]


async def test_checkpoint_runs_before_the_model_call_every_turn(store):
    llm = _OrderedLLM(store.calls)
    llm.queue(agent_turn("a"), agent_turn("b"))
    tenant = _tenant()
    engine = _engine(store, llm)

    first = await engine.handle_turn(tenant, None, "x", "test_chat")
    await engine.handle_turn(tenant, first.interaction_id, "y", "test_chat")

    assert store.calls == ["checkpoint", "generate", "checkpoint", "generate"]


async def test_blank_reply_is_invalid_and_goes_through_retry_and_fallback(store, llm):
    llm.queue(agent_turn("   "), agent_turn("\n\t"))

    result = await _engine(store, llm).handle_turn(_tenant(), None, "hi", "test_chat")

    assert len(llm.requests) == 2
    assert result.fallback is True and result.reply_text == INVALID_OUTPUT_FALLBACK


async def test_over_long_entity_is_invalid_output(store, llm):
    long_entity = agent_turn("ok", entities={"customer_name": "x" * 81})
    llm.queue(long_entity, agent_turn("fine", entities={"customer_name": "  Sam  "}))

    result = await _engine(store, llm).handle_turn(_tenant(), None, "hi", "test_chat")

    assert len(llm.requests) == 2 and result.fallback is False
    state = next(iter(store.interactions.values())).state
    assert state["entities"] == {"customer_name": "Sam"}
    assert state["failures"][0]["detail"] == "schema_invalid"


async def test_fallback_flag_is_on_the_message_and_survives_reload(store, llm):
    tenant = _tenant()
    engine = _engine(store, llm)
    llm.queue("bad", "bad", agent_turn("good"))

    first = await engine.handle_turn(tenant, None, "one", "test_chat")
    second = await engine.handle_turn(tenant, first.interaction_id, "two", "test_chat")
    transcript = await engine.transcript(tenant, first.interaction_id, "test_chat")

    assert [m.fallback for m in first.messages] == [False, False, True]  # greeting, you, agent
    assert [m.fallback for m in second.messages] == [False, False]
    assert [(m.sequence_number, m.fallback) for m in transcript.messages] == [
        (1, False),
        (2, False),
        (3, True),
        (4, False),
        (5, False),
    ]
    assert next(iter(store.interactions.values())).state["fallback_messages"] == [3]
