"""Unit tests for the conversation engine: in-memory store + scripted fake model, no DB."""

import asyncio
import time
import uuid
from datetime import UTC, datetime
from datetime import time as wall
from decimal import Decimal

import pytest

from core.retry import RetryPolicy
from core.tenant import Tenant
from integrations.llm.fake import FakeLLMClient, agent_turn
from integrations.llm.port import LLMUnavailableError, ModelReply
from models.business import Business
from models.user import User
from services import agent as agent_service
from services.business_tools import get_business_info_tool, get_services_tool
from services.conversation import (
    INVALID_OUTPUT_FALLBACK,
    MAX_TOOL_ROUNDS,
    PROVIDER_FALLBACK,
    ConversationEngine,
    InteractionEndedError,
    InteractionNotFoundError,
    no_info_fallback,
    quota_fallback,
)
from services.directory import BusinessDirectory
from services.faq_search import search_faq_tool
from services.knowledge import KnowledgeBase
from services.tools import ToolRegistry
from services.usage import ConcurrencyLimitedError, DailyLimit, UsageKind
from tests.services.memory_conversation_store import MemoryStore
from tests.services.memory_directory_store import MemoryDirectoryStore
from tests.services.memory_faq_store import MemoryFaqStore
from tests.services.memory_usage_store import MemoryUsageStore, guard, limits


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
    store,
    llm,
    *,
    history_limit: int = 20,
    tools: ToolRegistry | None = None,
    timeout: float = 5,
    usage=None,
):
    return ConversationEngine(
        store,
        llm,
        tools or ToolRegistry(),
        history_limit,
        timeout,
        usage=usage or guard(),
        llm_retry=RetryPolicy(attempts=2),  # no backoff sleeps in unit tests
    )


def _texts(request) -> list[tuple[str, str]]:
    return [(m.role, m.text) for m in request.messages]


async def test_new_chat_persists_the_owners_greeting_as_message_one(store, llm):
    tenant = _tenant()
    llm.queue(agent_turn("We're at 1 Main St.", intent="LOCATION", sources=["business_profile"]))

    result = await _engine(store, llm).handle_turn(tenant, None, "Where are you?", "test_chat")

    assert [(m.sequence_number, m.speaker) for m in result.messages] == [
        (1, "agent"),
        (2, "customer"),
        (3, "agent"),
    ]
    assert (
        result.messages[0].text
        == "Hi, thanks for contacting Alpha Salon! How can I help you today?"
    )
    assert result.reply_text == "We're at 1 Main St."
    assert result.intent == "LOCATION"
    assert result.fallback is False and result.ended is False and result.status == "active"
    # Gemini saw the greeting as the opening model message, then the question.
    assert _texts(llm.requests[0]) == [
        ("model", result.messages[0].text),
        ("user", "Where are you?"),
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
        return ModelReply(text="not json")


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

    # Admission (new chat only) is counted inside the transaction the checkpoint commits.
    assert store.calls == ["admission", "checkpoint", "generate", "checkpoint", "generate"]


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


# --- Usage limits (slice #5) -------------------------------------------------


def _biz(tenant: Tenant) -> Business:
    assert tenant.business is not None
    return tenant.business


def _limited(per_day: int = 10_000, per_business: int = 10_000):
    store = MemoryUsageStore()
    usage = guard(store, limits(gemini_request=DailyLimit(per_day, per_business)))
    return store, usage


async def test_every_model_call_is_metered_with_tokens(store, llm):
    usage_store, usage = _limited()
    tenant = _tenant()
    llm.queue(agent_turn("Hi"))

    await _engine(store, llm, usage=usage).handle_turn(tenant, None, "hello", "test_chat")

    business = _biz(tenant).id
    assert usage_store.count(UsageKind.GEMINI_REQUEST, business) == 1
    assert usage_store.count(UsageKind.GEMINI_TOKENS, business) == 120  # fake: 100 in + 20 out
    assert all(
        e.interaction_id is not None for e in usage_store.events
    )  # attributed to the conversation


async def test_hitting_the_limit_answers_with_the_quota_fallback_and_no_model_call(store, llm):
    _, usage = _limited(per_business=3)
    tenant = _tenant()
    _biz(tenant).phone = "+1 (312) 847-1928"
    engine = _engine(store, llm, usage=usage)
    llm.queue(agent_turn("1"), agent_turn("2"), agent_turn("3"))

    first = await engine.handle_turn(tenant, None, "one", "test_chat")
    for text in ("two", "three"):
        await engine.handle_turn(tenant, first.interaction_id, text, "test_chat")
    fourth = await engine.handle_turn(tenant, first.interaction_id, "four", "test_chat")

    assert len(llm.requests) == 3  # the fourth message made zero Gemini calls
    assert fourth.fallback is True and fourth.usage_limited is True
    assert fourth.reply_text == quota_fallback(_biz(tenant))
    assert "+1 (312) 847-1928" in fourth.reply_text
    state = store.interactions[first.interaction_id].state
    assert state["requires_human"] is True
    assert state["failures"][-1]["kind"] == "usage_limited"
    assert state["failures"][-1]["detail"] == "gemini_request:business"
    assert state["usage_limited_messages"] == [fourth.messages[-1].sequence_number]


async def test_quota_fallback_without_contact_details_names_the_business():
    text = quota_fallback(_biz(_tenant("Alpha Salon")))
    assert "Please contact Alpha Salon directly" in text


async def test_a_usage_store_outage_fails_closed_with_the_provider_fallback(store, llm):
    usage_store, usage = _limited()
    usage_store.fail_reserve = True
    llm.queue(agent_turn("never sent"))

    result = await _engine(store, llm, usage=usage).handle_turn(_tenant(), None, "hi", "test_chat")

    assert len(llm.requests) == 0
    assert result.reply_text == PROVIDER_FALLBACK and result.usage_limited is False
    state = next(iter(store.interactions.values())).state
    assert state["failures"][-1]["detail"] == "usage_unavailable"


async def test_tokens_of_a_blocked_reply_are_metered(store, llm):
    usage_store, usage = _limited()
    tenant = _tenant()
    llm.queue(LLMUnavailableError("MAX_TOKENS", tokens_in=300, tokens_out=2048))

    await _engine(store, llm, usage=usage).handle_turn(tenant, None, "hi", "test_chat")

    assert usage_store.count(UsageKind.GEMINI_TOKENS, _biz(tenant).id) == 2348


async def test_a_limit_hit_on_the_invalid_output_retry_still_falls_back_safely(store, llm):
    _, usage = _limited(per_business=1)
    llm.queue("not json", agent_turn("never sent"))

    result = await _engine(store, llm, usage=usage).handle_turn(_tenant(), None, "hi", "test_chat")

    assert len(llm.requests) == 1
    assert result.usage_limited is True


async def test_failed_provider_calls_are_retried_per_policy_and_counted(store, llm):
    usage_store, usage = _limited()
    tenant = _tenant()
    flaky = LLMUnavailableError("503", retryable=True)
    llm.queue(flaky, flaky, agent_turn("never sent"))

    result = await _engine(store, llm, usage=usage).handle_turn(tenant, None, "hi", "test_chat")

    business = _biz(tenant).id
    assert len(llm.requests) == 2  # the policy's 2 attempts, then the provider fallback
    assert result.reply_text == PROVIDER_FALLBACK and result.usage_limited is False
    assert usage_store.count(UsageKind.GEMINI_REQUEST, business) == 2
    assert usage_store.count(UsageKind.FAILED_REQUEST, business) == 2


async def test_a_transient_failure_then_success_counts_both_attempts(store, llm):
    usage_store, usage = _limited()
    tenant = _tenant()
    llm.queue(LLMUnavailableError("429", retryable=True), agent_turn("Recovered"))

    result = await _engine(store, llm, usage=usage).handle_turn(tenant, None, "hi", "test_chat")

    assert result.reply_text == "Recovered"
    assert usage_store.count(UsageKind.GEMINI_REQUEST, _biz(tenant).id) == 2
    assert usage_store.count(UsageKind.FAILED_REQUEST, _biz(tenant).id) == 1


async def test_new_chats_are_refused_past_the_concurrency_limit(store, llm):
    usage_store = MemoryUsageStore()
    usage = guard(usage_store, limits())  # 10 per business
    tenant = _tenant()
    engine = _engine(store, llm, usage=usage)
    llm.queue(*[agent_turn(str(i)) for i in range(10)])
    started = [await engine.handle_turn(tenant, None, "hi", "test_chat") for _ in range(10)]

    with pytest.raises(ConcurrencyLimitedError):
        await engine.handle_turn(tenant, None, "one more", "test_chat")
    assert len(llm.requests) == 10

    # Continuing an existing chat is never subject to admission.
    llm.queue(agent_turn("still here"))
    await engine.handle_turn(tenant, started[0].interaction_id, "next", "test_chat")
    # Ending one frees a slot.
    await engine.end(tenant, started[0].interaction_id, "test_chat")
    llm.queue(agent_turn("welcome"))
    await engine.handle_turn(tenant, None, "now?", "test_chat")


# --- FAQ tool loop, operation log and grounding guard (slice #6) -------------


def _faqs_engine(store, llm, faqs: MemoryFaqStore, **kwargs):
    tools = ToolRegistry.of([search_faq_tool(KnowledgeBase(faqs))])
    return _engine(store, llm, tools=tools, **kwargs)


def _search(query: str = "walk in", text: str = "Let me check.") -> dict:
    return agent_turn(text, tool_calls=[{"name": "search_faq", "arguments": {"query": query}}])


def _salon(faqs: MemoryFaqStore, *, phone: str | None = None):
    tenant = _tenant()
    _biz(tenant).phone = phone
    walk_in = faqs.add(_biz(tenant).id, "Do you take walk-ins?", "Yes, before 3 PM.")
    return tenant, walk_in


async def test_faq_question_is_answered_from_a_search_faq_result(store):
    faqs = MemoryFaqStore()
    tenant, walk_in = _salon(faqs)
    llm = _OrderedLLM(store.calls)
    usage_store, usage = _limited()
    llm.queue(
        _search("walk in", text="CHECKING-NEVER-SHOWN"),
        agent_turn(
            "Yes, walk-ins are welcome before 3 PM.", intent="FAQ", sources=[str(walk_in.id)]
        ),
    )
    engine = _faqs_engine(store, llm, faqs, usage=usage)

    result = await engine.handle_turn(tenant, None, "can I just walk in?", "test_chat")

    assert result.reply_text == "Yes, walk-ins are welcome before 3 PM."
    assert result.intent == "FAQ" and result.fallback is False
    first, second = llm.requests
    # Tool-only retrieval: the first request carries no FAQ at all.
    assert "Yes, before 3 PM." not in first.system_instruction
    assert "Yes, before 3 PM." in second.system_instruction
    assert f"FAQ id: {walk_in.id}" in second.system_instruction
    assert "1. search_faq: succeeded" in second.system_instruction
    assert "Tool rounds used: 1 of 3." in second.system_instruction
    # Same conversation, rebuilt instruction; no new message roles.
    assert second.messages == first.messages and first.tools == second.tools
    assert [d.name for d in first.tools] == ["search_faq"]
    assert usage_store.count(UsageKind.GEMINI_REQUEST, _biz(tenant).id) == 2
    (op,) = store.operations
    assert op["tool_name"] == "search_faq" and op["status"] == "succeeded"
    assert op["arguments"] == {"query": "walk in"}
    assert op["result"] == {"faq_ids": [str(walk_in.id)], "count": 1}
    assert op["interaction_id"] == result.interaction_id
    # Logged and committed before the model hears about it; nothing open across a call.
    assert store.calls == [
        "admission",
        "checkpoint",
        "generate",
        "savepoint",
        "record_operation",
        "checkpoint",
        "generate",
    ]
    assert "CHECKING-NEVER-SHOWN" not in {m.message_text for m in store.messages}
    state = store.interactions[result.interaction_id].state
    assert "failures" not in state and state["requires_human"] is False


async def test_editing_an_faq_changes_the_next_turns_faq_results(store, llm):
    faqs = MemoryFaqStore()
    tenant, walk_in = _salon(faqs)
    engine = _faqs_engine(store, llm, faqs)
    cite = {"intent": "FAQ", "sources": [str(walk_in.id)]}
    llm.queue(_search(), agent_turn("Before 3 PM.", **cite))
    first = await engine.handle_turn(tenant, None, "walk in?", "test_chat")

    walk_in.answer = "Yes, but only before noon."
    llm.queue(_search(), agent_turn("Before noon.", **cite))
    second = await engine.handle_turn(tenant, first.interaction_id, "walk in today?", "test_chat")

    turn_two_results = llm.requests[3].system_instruction
    assert "Yes, but only before noon." in turn_two_results
    assert "Yes, before 3 PM." not in turn_two_results
    assert second.reply_text == "Before noon." and second.fallback is False


async def test_a_disabled_faq_never_reaches_a_request_or_tool_result(store, llm):
    faqs = MemoryFaqStore()
    tenant, walk_in = _salon(faqs)
    walk_in.active = False
    llm.queue(
        _search(),
        agent_turn("Yes, before 3 PM.", intent="FAQ", sources=[str(walk_in.id)]),
    )

    result = await _faqs_engine(store, llm, faqs).handle_turn(tenant, None, "walk in?", "test_chat")

    for request in llm.requests:
        assert "Yes, before 3 PM." not in request.system_instruction
        assert str(walk_in.id) not in request.system_instruction
    assert "no matching FAQs" in llm.requests[1].system_instruction
    assert store.operations[0]["result"] == {"faq_ids": [], "count": 0}
    # Citing it anyway is ungrounded: the made-up answer never reaches the customer.
    assert result.reply_text == no_info_fallback(_biz(tenant))
    assert "Yes, before 3 PM." not in {m.message_text for m in store.messages}


RETRIEVED = "<the id search_faq returned>"
UNGROUNDED = [
    ("no_sources", [_search(), agent_turn("Sure!", intent="FAQ")], "missing_sources"),
    (
        "unknown_id",
        [_search(), agent_turn("Sure!", intent="FAQ", sources=[str(uuid.uuid4())])],
        "unknown_source",
    ),
    (
        "garbage_id",
        [_search(), agent_turn("Sure!", intent="FAQ", sources=["the walk-in one"])],
        "unknown_source",
    ),
    ("no_search", [agent_turn("Sure!", intent="FAQ", sources=[RETRIEVED])], "unknown_source"),
    (
        "hours_without_search",
        [agent_turn("We're open 9 to 5.", intent="BUSINESS_HOURS")],
        "missing_sources",
    ),
    (
        "price_without_sources",
        [_search(), agent_turn("A cut is $40.", intent="PRICE_INQUIRY")],
        "missing_sources",
    ),
    (
        "other_intent_citing_unretrieved",
        [agent_turn("Sure!", intent="PRICE_INQUIRY", sources=[str(uuid.uuid4())])],
        "unknown_source",
    ),
]


@pytest.mark.parametrize(
    ("script", "detail"), [c[1:] for c in UNGROUNDED], ids=[c[0] for c in UNGROUNDED]
)
async def test_an_ungrounded_answer_is_replaced_by_the_no_info_fallback(store, llm, script, detail):
    faqs = MemoryFaqStore()
    tenant, walk_in = _salon(faqs, phone="+1 (312) 847-1928")
    llm.queue(
        *[
            {**s, "sources": [str(walk_in.id) if x == RETRIEVED else x for x in s["sources"]]}
            for s in script
        ]
    )

    result = await _faqs_engine(store, llm, faqs).handle_turn(tenant, None, "walk in?", "test_chat")

    assert result.reply_text == no_info_fallback(_biz(tenant))
    assert "+1 (312) 847-1928" in result.reply_text
    assert result.fallback is True and result.intent == "UNKNOWN" and not result.usage_limited
    assert "Sure!" not in {m.message_text for m in store.messages}
    state = store.interactions[result.interaction_id].state
    assert state["requires_human"] is True
    assert (state["failures"][-1]["kind"], state["failures"][-1]["detail"]) == (
        "ungrounded",
        detail,
    )
    assert state["fallback_messages"] == [result.messages[-1].sequence_number]
    assert "clarification_attempts" not in state


async def test_grounded_intents_accept_cited_faqs_and_location_cites_the_profile(store, llm):
    faqs = MemoryFaqStore()
    tenant, walk_in = _salon(faqs)
    engine = _faqs_engine(store, llm, faqs)
    llm.queue(
        _search("hours"),
        agent_turn("Walk-ins before 3 PM.", intent="BUSINESS_HOURS", sources=[str(walk_in.id)]),
    )
    hours = await engine.handle_turn(tenant, None, "when can I come?", "test_chat")

    # The address is in BUSINESS FACTS, so a LOCATION answer cites the profile, no FAQ.
    llm.queue(agent_turn("We're at 1 Main St.", intent="LOCATION", sources=["business_profile"]))
    where = await engine.handle_turn(tenant, hours.interaction_id, "where are you?", "test_chat")

    assert (hours.fallback, hours.reply_text) == (False, "Walk-ins before 3 PM.")
    assert (where.fallback, where.reply_text) == (False, "We're at 1 Main St.")


async def test_an_faq_id_from_an_earlier_turn_does_not_ground_this_one(store, llm):
    faqs = MemoryFaqStore()
    tenant, walk_in = _salon(faqs)
    engine = _faqs_engine(store, llm, faqs)
    cite = {"intent": "FAQ", "sources": [str(walk_in.id)]}
    llm.queue(_search(), agent_turn("Before 3 PM.", **cite))
    first = await engine.handle_turn(tenant, None, "walk in?", "test_chat")

    llm.queue(agent_turn("Still before 3 PM.", **cite))  # no search this turn
    second = await engine.handle_turn(tenant, first.interaction_id, "and today?", "test_chat")

    assert first.fallback is False
    assert second.reply_text == no_info_fallback(_biz(tenant)) and second.fallback is True
    failure = store.interactions[first.interaction_id].state["failures"][-1]
    assert (failure["kind"], failure["detail"]) == ("ungrounded", "unknown_source")


async def test_no_info_fallback_points_to_the_contacts_and_promises_nothing():
    tenant = _tenant("Alpha Salon")
    assert no_info_fallback(_biz(tenant)) == (
        "Sorry, I don't have that information. Please contact Alpha Salon directly."
    )
    _biz(tenant).phone, _biz(tenant).contact_email = "+1 555 0100", "team@alpha.test"
    assert no_info_fallback(_biz(tenant)) == (
        "Sorry, I don't have that information. "
        "You can reach the team at +1 555 0100 or team@alpha.test."
    )


async def test_an_honest_no_answer_after_an_empty_search_is_kept(store, llm):
    faqs = MemoryFaqStore()
    tenant, walk_in = _salon(faqs)
    walk_in.active = False  # nothing searchable at all
    honest = "Sorry, I don't have that information. Please contact Alpha Salon directly."
    llm.queue(_search("parking validation"), agent_turn(honest, intent="UNKNOWN"))

    result = await _faqs_engine(store, llm, faqs).handle_turn(
        tenant, None, "do you validate parking?", "test_chat"
    )

    assert result.reply_text == honest and result.fallback is False


async def test_invalid_tool_args_are_rejected_logged_and_the_loop_continues(store, llm):
    faqs = MemoryFaqStore()
    tenant, walk_in = _salon(faqs)
    llm.queue(
        agent_turn("x", tool_calls=[{"name": "search_faq", "arguments": {"query": "   "}}]),
        _search(),
        agent_turn("Before 3 PM.", intent="FAQ", sources=[str(walk_in.id)]),
    )

    result = await _faqs_engine(store, llm, faqs).handle_turn(tenant, None, "walk in?", "test_chat")

    assert result.reply_text == "Before 3 PM." and result.fallback is False
    assert len(llm.requests) == 3
    assert [op["status"] for op in store.operations] == ["rejected", "succeeded"]
    rejected = store.operations[0]
    assert rejected["arguments"] == {"argument_names": ["query"]}
    assert rejected["result"] == {"reason": "invalid_arguments", "fields": ["query"]}
    assert faqs.reads == 2  # only the valid call reached the store (count, then list)
    second = llm.requests[1].system_instruction
    assert '1. search_faq: rejected {"error": "invalid_arguments", "fields": ["query"]}' in second
    assert "Yes, before 3 PM." not in second


async def test_a_failing_tool_is_reported_as_unavailable_and_logged(store, llm):
    faqs = MemoryFaqStore()
    tenant, _ = _salon(faqs)
    faqs.fail = True
    llm.queue(_search(), agent_turn("Sorry, I can't check that right now.", intent="UNKNOWN"))

    result = await _faqs_engine(store, llm, faqs).handle_turn(tenant, None, "walk in?", "test_chat")

    assert result.fallback is False
    assert store.operations[0]["status"] == "failed"
    assert store.operations[0]["result"] == {"reason": "executor_error"}
    second = llm.requests[1].system_instruction
    assert '1. search_faq: failed {"error": "tool_unavailable"}' in second


async def test_the_tool_loop_stops_when_the_model_keeps_requesting_tools(store, llm):
    faqs = MemoryFaqStore()
    tenant, _ = _salon(faqs)
    llm.queue(*[_search(f"q{i}") for i in range(MAX_TOOL_ROUNDS + 2)])

    result = await _faqs_engine(store, llm, faqs).handle_turn(tenant, None, "walk in?", "test_chat")

    assert len(llm.requests) == MAX_TOOL_ROUNDS + 1  # 3 rounds, then the reply that asked again
    assert len(llm.script) == 1  # never called again
    assert len(store.operations) == MAX_TOOL_ROUNDS
    assert "No more tool calls" in llm.requests[-1].system_instruction
    assert result.reply_text == no_info_fallback(_biz(tenant)) and result.fallback is True
    state = store.interactions[result.interaction_id].state
    assert state["requires_human"] is True
    failure = state["failures"][-1]
    assert (failure["kind"], failure["detail"]) == ("tool_rounds_exhausted", "max_tool_rounds")


async def test_total_model_calls_stay_bounded_with_the_invalid_output_retry(store, llm):
    faqs = MemoryFaqStore()
    tenant, _ = _salon(faqs)
    llm.queue("not json", *[_search(f"q{i}") for i in range(MAX_TOOL_ROUNDS + 3)])

    await _faqs_engine(store, llm, faqs).handle_turn(tenant, None, "walk in?", "test_chat")

    assert len(llm.requests) == MAX_TOOL_ROUNDS + 2  # + final + the one invalid-output retry


async def test_a_quota_hit_mid_loop_answers_with_the_quota_fallback_and_stops(store, llm):
    faqs = MemoryFaqStore()
    tenant, _ = _salon(faqs)
    _, usage = _limited(per_business=1)
    llm.queue(_search(), agent_turn("never sent"))

    result = await _faqs_engine(store, llm, faqs, usage=usage).handle_turn(
        tenant, None, "walk in?", "test_chat"
    )

    assert len(llm.requests) == 1
    assert result.usage_limited is True and result.reply_text == quota_fallback(_biz(tenant))
    assert [op["status"] for op in store.operations] == ["succeeded"]  # already logged
    assert store.interactions[result.interaction_id].state["requires_human"] is True


class _SlowAfterFirstLLM(FakeLLMClient):
    async def generate(self, request):
        if self.requests:
            await asyncio.sleep(1)
        return await super().generate(request)


async def test_the_turn_deadline_covers_every_round_of_the_tool_loop(store):
    faqs = MemoryFaqStore()
    tenant, _ = _salon(faqs)
    llm = _SlowAfterFirstLLM()
    llm.queue(_search(), agent_turn("never seen"))

    result = await _faqs_engine(store, llm, faqs, timeout=0.1).handle_turn(
        tenant, None, "walk in?", "test_chat"
    )

    assert result.reply_text == PROVIDER_FALLBACK and result.fallback is True
    assert store.calls.count("checkpoint") == 2  # the op-log row was committed first
    assert len(store.operations) == 1
    failure = store.interactions[result.interaction_id].state["failures"][-1]
    assert (failure["kind"], failure["detail"]) == ("provider_unavailable", "turn_timeout")


# --- Business facts: hours, location, services and prices (slice #7) ---------

# Wednesday 2026-10-21, 11:00 in New York.
SALON_NOW = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)
SALON_WEEK = {
    0: None,
    1: ("09:00", "18:00"),
    2: ("09:00", "18:00"),
    3: ("09:00", "20:00"),
    4: ("09:00", "18:00"),
    5: ("10:00", "16:00"),  # Saturday
    6: None,
}


class _RecordingDirectoryStore(MemoryDirectoryStore):
    """Appends "directory" to the conversation store's call log on every read."""

    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def list_services(self, business_id, *, active_only):
        self._calls.append("directory")
        return await super().list_services(business_id, active_only=active_only)

    async def list_hours(self, business_id):
        self._calls.append("directory")
        return await super().list_hours(business_id)


class _Salon:
    """A tenant with a catalog (Haircut, Kids cut, Beard trim, an inactive Hot towel
    shave) and weekly hours, and the engine wired with all three tools."""

    def __init__(self, store: MemoryStore, *, hours: bool = True) -> None:
        self.tenant = _tenant()
        business = _biz(self.tenant)
        business.phone, business.currency = "+1 (312) 847-1928", "USD"
        self.directory_store = _RecordingDirectoryStore(store.calls)
        self.faqs = MemoryFaqStore()
        add = self.directory_store.add_service
        self.haircut = add(business.id, "Haircut", duration_minutes=45, price="40.00")
        self.kids = add(business.id, "Kids cut", duration_minutes=20, price="18.00")
        self.beard = add(business.id, "Beard trim", duration_minutes=15, price="15.00")
        self.shave = add(
            business.id, "Hot towel shave", duration_minutes=30, price="30.00", active=False
        )
        if hours:
            self.directory_store.set_week(business.id, SALON_WEEK)
        self.directory = BusinessDirectory(self.directory_store, lambda: SALON_NOW)

    def engine(self, store: MemoryStore, llm, **kwargs) -> ConversationEngine:
        tools = ToolRegistry.of(
            [
                search_faq_tool(KnowledgeBase(self.faqs)),
                get_services_tool(self.directory),
                get_business_info_tool(self.directory),
            ]
        )
        return ConversationEngine(
            store,
            llm,
            tools,
            20,
            5,
            usage=kwargs.pop("usage", None) or guard(),
            llm_retry=RetryPolicy(attempts=2),
            directory=self.directory,
        )

    @property
    def fallback(self) -> str:
        return no_info_fallback(_biz(self.tenant))


def _services(query: str | None = None) -> dict:
    arguments = {"query": query} if query is not None else {}
    return agent_turn("Checking.", tool_calls=[{"name": "get_services", "arguments": arguments}])


def _failure_of(store: MemoryStore, result) -> tuple[str, str]:
    failure = store.interactions[result.interaction_id].state["failures"][-1]
    return failure["kind"], failure["detail"]


def _hours_block(prompt: str) -> str:
    start, end = "<<<BUSINESS_HOURS>>>", "<<<END_BUSINESS_HOURS>>>"
    return prompt[prompt.index(start) : prompt.index(end)]


async def test_saturday_hours_are_answered_from_the_prompt_in_one_call(store, llm):
    salon = _Salon(store)
    llm.queue(
        agent_turn(
            "On Saturday we're open 10 AM to 4 PM.",
            intent="BUSINESS_HOURS",
            sources=["business_hours"],
        )
    )

    result = await salon.engine(store, llm).handle_turn(
        salon.tenant, None, "When are you open Saturday?", "test_chat"
    )

    assert len(llm.requests) == 1 and store.operations == []
    assert result.reply_text == "On Saturday we're open 10 AM to 4 PM." and not result.fallback
    hours = _hours_block(llm.requests[0].system_instruction)
    assert "Today: Wednesday 2026-10-21" in hours
    assert "Saturday: 10:00-16:00" in hours and "Monday: closed" in hours
    assert "Saturday 2026-10-24: 10:00-16:00" in hours
    assert "Tuesday 2026-10-27: 09:00-18:00" in hours  # 7 days: today through next Tuesday
    assert "Wednesday 2026-10-28" not in hours
    # Services never ride in the prompt: only get_services brings them.
    assert "Haircut" not in llm.requests[0].system_instruction
    assert "Currency: USD" in llm.requests[0].system_instruction


async def test_location_cites_the_business_profile_and_needs_a_source(store, llm):
    salon = _Salon(store)
    engine = salon.engine(store, llm)
    llm.queue(
        agent_turn("We're at 1 Main St.", intent="LOCATION", sources=["business_profile"]),
        agent_turn("We're at 1 Main St.", intent="LOCATION"),
    )

    where = await engine.handle_turn(salon.tenant, None, "Where are you?", "test_chat")
    again = await engine.handle_turn(salon.tenant, where.interaction_id, "Where?", "test_chat")

    assert (where.fallback, where.reply_text) == (False, "We're at 1 Main St.")
    assert again.reply_text == salon.fallback
    assert _failure_of(store, again) == ("ungrounded", "missing_sources")


async def test_beard_trim_price_comes_from_get_services_and_cites_the_service(store, llm):
    salon = _Salon(store)
    reply = "A beard trim is $15 and takes about 15 minutes."
    llm.queue(
        _services("beard trim"),
        agent_turn(reply, intent="PRICE_INQUIRY", sources=[str(salon.beard.id)]),
    )

    result = await salon.engine(store, llm).handle_turn(
        salon.tenant, None, "How much is a beard trim?", "test_chat"
    )

    assert (result.fallback, result.reply_text) == (False, reply)
    first, second = llm.requests
    assert "15.00" not in first.system_instruction
    assert '1. get_services: succeeded {"currency": "USD", "match": "found"' in (
        second.system_instruction
    )
    assert '"price": "15.00"' in second.system_instruction
    (op,) = store.operations
    assert (op["tool_name"], op["status"]) == ("get_services", "succeeded")
    assert op["arguments"] == {"query": "beard trim"}
    assert op["result"] == {"match": "found", "service_ids": [str(salon.beard.id)]}


async def test_a_perm_is_not_listed_so_an_honest_reply_stands_and_a_price_never_does(store, llm):
    salon = _Salon(store)
    engine = salon.engine(store, llm)
    honest = "Sorry, we don't list a perm. Anything else I can help with?"
    llm.queue(
        _services("perm"),
        agent_turn(honest, intent="PRICE_INQUIRY"),
        _services("perm"),
        agent_turn("A perm is usually around $80.", intent="PRICE_INQUIRY"),
    )

    first = await engine.handle_turn(salon.tenant, None, "How much is a perm?", "test_chat")
    second = await engine.handle_turn(salon.tenant, first.interaction_id, "Roughly?", "test_chat")

    assert (first.fallback, first.reply_text) == (False, honest)
    assert '"match": "not_found"' in llm.requests[1].system_instruction
    assert second.reply_text == salon.fallback and second.fallback is True
    assert _failure_of(store, second) == ("ungrounded", "unverified_amount")
    assert "$80" not in {m.message_text for m in store.messages}
    assert store.interactions[first.interaction_id].state["requires_human"] is True


async def test_cut_is_ambiguous_and_the_agent_asks_which_one(store, llm):
    salon = _Salon(store)
    engine = salon.engine(store, llm)
    ask = "Do you mean a Haircut or a Kids cut?"
    llm.queue(
        _services("cut"),
        agent_turn(ask, intent="PRICE_INQUIRY"),
        _services("cut"),
        agent_turn("A cut is $40.", intent="PRICE_INQUIRY", sources=[str(salon.haircut.id)]),
    )

    first = await engine.handle_turn(salon.tenant, None, "How much is a cut?", "test_chat")
    second = await engine.handle_turn(salon.tenant, first.interaction_id, "A cut", "test_chat")

    assert (first.fallback, first.reply_text) == (False, ask)
    results = llm.requests[1].system_instruction
    assert '"match": "ambiguous"' in results
    assert "Haircut" in results and "Kids cut" in results and "40.00" not in results
    # The candidates carry no prices, so quoting one before a lookup that finds it fails.
    assert second.reply_text == salon.fallback
    assert _failure_of(store, second) == ("ungrounded", "unverified_amount")


async def test_an_inactive_service_is_never_offered_or_priced(store, llm):
    salon = _Salon(store)
    llm.queue(
        _services(),
        _services("hot towel shave"),
        agent_turn("A hot towel shave is $30.", intent="PRICE_INQUIRY"),
    )

    result = await salon.engine(store, llm).handle_turn(
        salon.tenant, None, "Do you do hot towel shaves?", "test_chat"
    )

    for request in llm.requests:
        assert "Hot towel shave" not in request.system_instruction
        assert "30.00" not in request.system_instruction
    assert '"match": "not_found"' in llm.requests[2].system_instruction
    assert result.reply_text == salon.fallback
    # The listing returned real prices, so the not_found exemption no longer applies.
    assert _failure_of(store, result) == ("ungrounded", "missing_sources")


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        ("A haircut is $40.00 and takes 45 minutes.", None),
        ("A haircut is forty dollars.", None),
        ("A haircut is $45.", "unverified_amount"),
        ("A haircut is $40, and a kids cut is $18.", "unverified_amount"),
        ("A haircut is $40 and takes about an hour.", "unverified_duration"),
    ],
)
async def test_prices_and_durations_must_equal_the_stored_ones(store, llm, reply, detail):
    salon = _Salon(store)
    llm.queue(
        _services("haircut"),
        agent_turn(reply, intent="PRICE_INQUIRY", sources=[str(salon.haircut.id)]),
    )

    result = await salon.engine(store, llm).handle_turn(
        salon.tenant, None, "How much is a haircut?", "test_chat"
    )

    if detail is None:
        assert (result.fallback, result.reply_text) == (False, reply)
    else:
        assert result.reply_text == salon.fallback
        assert _failure_of(store, result) == ("ungrounded", detail)


async def test_listing_services_grounds_every_listed_price(store, llm):
    salon = _Salon(store)
    reply = "A haircut is $40 (45 min), a kids cut $18 and a beard trim $15."
    sources = [str(s.id) for s in (salon.haircut, salon.kids, salon.beard)]
    llm.queue(_services(), agent_turn(reply, intent="PRICE_INQUIRY", sources=sources))

    result = await salon.engine(store, llm).handle_turn(
        salon.tenant, None, "What are your prices?", "test_chat"
    )

    assert (result.fallback, result.reply_text) == (False, reply)
    assert store.operations[0]["result"]["match"] == "list"


async def test_amounts_from_an_faq_answer_retrieved_this_turn_are_accepted(store, llm):
    salon = _Salon(store)
    kids = salon.faqs.add(
        _biz(salon.tenant).id,
        "Do you offer a children's haircut?",
        "Yes, children under 12 get a cut for 18 dollars, about 20 minutes.",
    )
    llm.queue(
        _search("children haircut"),
        agent_turn("Yes: $18, about 20 minutes.", intent="FAQ", sources=[str(kids.id)]),
    )

    result = await salon.engine(store, llm).handle_turn(
        salon.tenant, None, "Do you cut kids' hair?", "test_chat"
    )

    assert (result.fallback, result.reply_text) == (False, "Yes: $18, about 20 minutes.")


async def test_hours_not_set_are_never_called_closed_or_cited(store, llm):
    salon = _Salon(store, hours=False)
    engine = salon.engine(store, llm)
    honest = "Our opening hours aren't listed yet. You can call us at +1 (312) 847-1928."
    llm.queue(
        agent_turn(honest, intent="BUSINESS_HOURS", sources=["business_profile"]),
        agent_turn("We're closed Saturday.", intent="BUSINESS_HOURS", sources=["business_hours"]),
    )

    first = await engine.handle_turn(salon.tenant, None, "Open Saturday?", "test_chat")
    # Citing the profile (the phone number) isn't grounds for an hours answer: the
    # customer gets the fixed no-info line with the same contacts, flagged for a human.
    assert first.reply_text == salon.fallback
    assert _failure_of(store, first) == ("ungrounded", "unsupported_source")
    second = await engine.handle_turn(salon.tenant, first.interaction_id, "Sure?", "test_chat")
    # And with no listed hours there is nothing to cite.
    assert second.reply_text == salon.fallback
    assert _failure_of(store, second) == ("ungrounded", "unknown_source")

    hours = _hours_block(llm.requests[0].system_instruction)
    assert "Opening hours: not listed" in hours and "closed" not in hours
    assert "Today: Wednesday 2026-10-21" in hours


async def test_a_date_beyond_the_prompt_week_is_looked_up_with_get_business_info(store, llm):
    salon = _Salon(store)
    llm.queue(
        agent_turn(
            "Checking.",
            tool_calls=[{"name": "get_business_info", "arguments": {"date": "2026-12-05"}}],
        ),
        agent_turn(
            "On Saturday, December 5 we're open 10 AM to 4 PM.",
            intent="BUSINESS_HOURS",
            sources=["business_hours"],
        ),
    )

    result = await salon.engine(store, llm).handle_turn(
        salon.tenant, None, "Are you open December 5th?", "test_chat"
    )

    assert result.fallback is False and len(llm.requests) == 2
    assert '"date": "2026-12-05"' in llm.requests[1].system_instruction
    assert '"opens": "10:00"' in llm.requests[1].system_instruction
    op = store.operations[0]
    assert (op["tool_name"], op["arguments"]) == (
        "get_business_info",
        {"date": "2026-12-05", "days": None},
    )


async def test_a_service_id_from_an_earlier_turn_does_not_ground_this_one(store, llm):
    salon = _Salon(store)
    engine = salon.engine(store, llm)
    cite = {"intent": "PRICE_INQUIRY", "sources": [str(salon.beard.id)]}
    llm.queue(
        _services("beard trim"),
        agent_turn("A beard trim is $15.", **cite),
        agent_turn("Still $15.", **cite),  # no lookup this turn
    )

    first = await engine.handle_turn(salon.tenant, None, "Beard trim price?", "test_chat")
    second = await engine.handle_turn(salon.tenant, first.interaction_id, "Sure?", "test_chat")

    assert first.fallback is False
    assert second.reply_text == salon.fallback
    assert _failure_of(store, second) == ("ungrounded", "unknown_source")


async def test_owner_edits_to_prices_and_hours_apply_to_the_next_turn(store, llm):
    salon = _Salon(store)
    engine = salon.engine(store, llm)
    cite = {"intent": "PRICE_INQUIRY", "sources": [str(salon.beard.id)]}
    llm.queue(_services("beard trim"), agent_turn("A beard trim is $15.", **cite))
    first = await engine.handle_turn(salon.tenant, None, "Beard trim price?", "test_chat")

    salon.beard.price = Decimal("17.00")
    saturday = next(h for h in salon.directory_store.hours if h.day_of_week == 5)
    saturday.opens_at, saturday.closes_at = wall(11), wall(15)
    llm.queue(_services("beard trim"), agent_turn("A beard trim is $15.", **cite))
    stale = await engine.handle_turn(salon.tenant, first.interaction_id, "Again?", "test_chat")
    llm.queue(_services("beard trim"), agent_turn("A beard trim is $17.", **cite))
    fresh = await engine.handle_turn(salon.tenant, first.interaction_id, "Now?", "test_chat")

    assert first.reply_text == "A beard trim is $15."
    assert stale.reply_text == salon.fallback
    assert _failure_of(store, stale) == ("ungrounded", "unverified_amount")
    assert fresh.reply_text == "A beard trim is $17." and fresh.fallback is False
    turn_two = llm.requests[2].system_instruction
    assert "Saturday 2026-10-24: 11:00-15:00" in turn_two
    assert "Saturday 2026-10-24: 10:00-16:00" not in turn_two
    assert '"price": "17.00"' in llm.requests[3].system_instruction


async def test_business_facts_are_read_before_the_first_checkpoint(store):
    salon = _Salon(store)
    llm = _OrderedLLM(store.calls)
    llm.queue(_services("beard trim"), agent_turn("Sure."))

    await salon.engine(store, llm).handle_turn(salon.tenant, None, "Beard trim?", "test_chat")

    assert store.calls == [
        "admission",
        "directory",  # weekly hours
        "directory",  # the 7-day schedule
        "checkpoint",
        "generate",
        "savepoint",
        "directory",  # get_services, inside its savepoint
        "record_operation",
        "checkpoint",
        "generate",
    ]


async def test_a_get_business_info_date_out_of_range_is_rejected_and_the_turn_continues(store, llm):
    salon = _Salon(store)
    llm.queue(
        agent_turn(
            "Checking.",
            tool_calls=[{"name": "get_business_info", "arguments": {"date": "2020-01-01"}}],
        ),
        agent_turn("I can only check dates from today on.", intent="UNKNOWN"),
    )

    result = await salon.engine(store, llm).handle_turn(
        salon.tenant, None, "Were you open on Jan 1 2020?", "test_chat"
    )

    assert result.fallback is False
    assert store.operations[0]["status"] == "rejected"
    assert '"error": "date_out_of_range"' in llm.requests[1].system_instruction
