"""The lifecycle rules (pure) and `InteractionLifecycle` over the in-memory store."""

import itertools
import uuid
from datetime import timedelta

import pytest

from core.tenant import Tenant
from models.business import Business
from models.user import User
from services.lifecycle import (
    ClosingFacts,
    InteractionLifecycle,
    InteractionNotFoundError,
    close_outcome,
    primary_intent,
    record_intent,
)
from tests.services.memory_conversation_store import NOW, MemoryStore

# --- pure rules ---------------------------------------------------------------------


def _facts(booked=False, failed=False, fallback=False, human=False) -> ClosingFacts:
    return ClosingFacts(
        has_confirmed_booking=booked,
        booking_failed=failed,
        last_agent_message_was_fallback=fallback,
        requires_human=human,
    )


@pytest.mark.parametrize(
    ("facts", "reason", "expected"),
    [
        (_facts(), "ended", ("completed", "answered")),
        (_facts(booked=True), "ended", ("completed", "booked")),
        (_facts(human=True), "ended", ("completed", "escalated")),
        (_facts(failed=True), "ended", ("failed", "failed")),
        (_facts(fallback=True), "ended", ("failed", "failed")),
        # booked beats every failure signal, even a failed attempt before the success
        (
            _facts(booked=True, failed=True, fallback=True, human=True),
            "ended",
            ("completed", "booked"),
        ),
        # failed beats escalated
        (_facts(failed=True, human=True), "ended", ("failed", "failed")),
        (_facts(fallback=True, human=True), "ended", ("failed", "failed")),
        # idle
        (_facts(), "idle", ("abandoned", "abandoned")),
        (_facts(human=True), "idle", ("abandoned", "escalated")),
        (_facts(booked=True), "idle", ("abandoned", "booked")),
        (_facts(failed=True), "idle", ("abandoned", "abandoned")),
        (_facts(fallback=True, human=True), "idle", ("abandoned", "escalated")),
    ],
    ids=[
        "answered",
        "booked",
        "escalated",
        "booking-failed",
        "fallback-last",
        "booked-beats-all",
        "failed-beats-escalated",
        "fallback-beats-escalated",
        "idle-plain",
        "idle-escalated",
        "idle-booked",
        "idle-failed-booking",
        "idle-fallback-escalated",
    ],
)
def test_close_outcome_examples(facts, reason, expected):
    assert close_outcome(facts, reason) == expected


def test_close_outcome_over_every_combination_keeps_the_invariants():
    for booked, failed, fallback, human, reason in itertools.product(
        (False, True), (False, True), (False, True), (False, True), ("ended", "idle")
    ):
        status, outcome = close_outcome(_facts(booked, failed, fallback, human), reason)
        assert status != "in_progress"
        assert (status == "abandoned") == (reason == "idle")
        assert (outcome == "booked") == booked
        assert (status == "failed") == (reason == "ended" and not booked and (failed or fallback))
        if status == "failed":
            assert outcome == "failed"
        if outcome == "escalated":
            assert human and not booked and status != "failed"
        if outcome == "answered":
            assert reason == "ended" and not human and status == "completed"


@pytest.mark.parametrize(
    ("intents", "expected"),
    [
        ([], None),
        (["FAQ"], "FAQ"),
        (["FAQ", "BOOK_APPOINTMENT", "UNKNOWN"], "BOOK_APPOINTMENT"),
        (["UNKNOWN", "FAQ"], "FAQ"),
        (["LOCATION", "BUSINESS_HOURS"], "BUSINESS_HOURS"),
        (["PRICE_INQUIRY", "HUMAN_REQUEST"], "HUMAN_REQUEST"),
        (["COMPLAINT", "URGENT"], "URGENT"),
        (["CANCEL", "RESCHEDULE"], "RESCHEDULE"),
        (["CANCEL", "COMPLAINT"], "CANCEL"),
        (["SOMETHING_NEW"], "SOMETHING_NEW"),
    ],
    ids=[
        "none",
        "single",
        "book-wins",
        "faq-over-unknown",
        "hours-over-location",
        "human-over-price",
        "urgent-over-complaint",
        "reschedule-over-cancel",
        "cancel-over-complaint",
        "unlisted-falls-back-to-first",
    ],
)
def test_primary_intent(intents, expected):
    assert primary_intent(intents) == expected


def test_primary_intent_follows_the_documented_priority_order():
    order = [
        "BOOK_APPOINTMENT",
        "RESCHEDULE",
        "CANCEL",
        "URGENT",
        "COMPLAINT",
        "HUMAN_REQUEST",
        "PRICE_INQUIRY",
        "BUSINESS_HOURS",
        "LOCATION",
        "FAQ",
        "UNKNOWN",
    ]
    for index, expected in enumerate(order):
        assert primary_intent(list(reversed(order[index:]))) == expected


def test_record_intent_is_distinct_ordered_and_capped():
    assert record_intent(["FAQ"], "FAQ") == ["FAQ"]
    assert record_intent(["FAQ"], "LOCATION") == ["FAQ", "LOCATION"]
    full = [f"I{n}" for n in range(20)]
    assert record_intent(full, "NEW") == full
    assert record_intent(full, "I3") == full


# --- the lifecycle over the in-memory store ------------------------------------------


class FakeSummarizer:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[uuid.UUID, uuid.UUID, bool]] = []
        self.states_seen: list[str] = []
        self.fail = fail
        self.store: MemoryStore | None = None

    async def summarize(self, business_id, interaction_id, *, ai=True):
        self.calls.append((business_id, interaction_id, ai))
        if self.store is not None:
            self.states_seen.append(self.store.interactions[interaction_id].status)
        if self.fail:
            raise RuntimeError("model said: secret transcript text")
        return None


def _bid(tenant: Tenant) -> uuid.UUID:
    assert tenant.business is not None
    return tenant.business.id


def _tenant() -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone="America/New_York",
        phone=None,
        contact_email=None,
    )
    return Tenant(user=user, business=business)


async def _open(store: MemoryStore, tenant: Tenant, **state):
    interaction = await store.create_interaction(_bid(tenant), "test_chat")
    interaction.state = state
    await store.append_message(interaction, "agent", "Hi")
    await store.append_message(interaction, "customer", "Hello")
    return interaction


def _lifecycle(store, summarizer, idle=15) -> InteractionLifecycle:
    summarizer.store = store
    return InteractionLifecycle(store, summarizer, idle_minutes=idle)


async def test_end_closes_commits_then_summarizes():
    store, summarizer, tenant = MemoryStore(), FakeSummarizer(), _tenant()
    interaction = await _open(store, tenant)
    store.signals[interaction.id] = (True, False)

    await _lifecycle(store, summarizer).end(tenant, interaction.id, "test_chat")

    assert (interaction.status, interaction.outcome) == ("completed", "booked")
    assert interaction.ended_at is not None
    assert summarizer.calls == [(_bid(tenant), interaction.id, True)]
    assert summarizer.states_seen == ["completed"]  # closed before the summarizer ran
    assert store.calls.index("checkpoint") >= 0


async def test_end_marks_a_last_fallback_reply_as_failed():
    store, summarizer, tenant = MemoryStore(), FakeSummarizer(), _tenant()
    interaction = await _open(store, tenant, fallback_messages=[3])
    await store.append_message(interaction, "agent", "Sorry, trouble")  # sequence 3

    await _lifecycle(store, summarizer).end(tenant, interaction.id, "test_chat")

    assert (interaction.status, interaction.outcome) == ("failed", "failed")


async def test_end_of_an_escalated_chat():
    store, summarizer, tenant = MemoryStore(), FakeSummarizer(), _tenant()
    interaction = await _open(store, tenant, requires_human=True)

    await _lifecycle(store, summarizer).end(tenant, interaction.id, "test_chat")

    assert (interaction.status, interaction.outcome) == ("completed", "escalated")


async def test_an_escalation_alone_makes_the_outcome_escalated():
    # The flag may have been cleared by a later turn; the recorded escalation still counts.
    store, summarizer, tenant = MemoryStore(), FakeSummarizer(), _tenant()
    interaction = await _open(store, tenant)
    store.escalated.add(interaction.id)

    await _lifecycle(store, summarizer).end(tenant, interaction.id, "test_chat")

    assert (interaction.status, interaction.outcome) == ("completed", "escalated")


async def test_ending_twice_changes_nothing_but_still_asks_for_the_summary():
    store, summarizer, tenant = MemoryStore(), FakeSummarizer(), _tenant()
    interaction = await _open(store, tenant)
    lifecycle = _lifecycle(store, summarizer)
    await lifecycle.end(tenant, interaction.id, "test_chat")
    ended_at = interaction.ended_at
    store.signals[interaction.id] = (True, False)  # later facts must not rewrite the close

    await lifecycle.end(tenant, interaction.id, "test_chat")

    assert (interaction.status, interaction.outcome) == ("completed", "answered")
    assert interaction.ended_at == ended_at
    assert len(summarizer.calls) == 2  # the summarizer itself no-ops when a summary exists


async def test_a_summarizer_failure_does_not_undo_the_close(caplog):
    store, tenant = MemoryStore(), _tenant()
    interaction = await _open(store, tenant)

    with caplog.at_level("WARNING"):
        await _lifecycle(store, FakeSummarizer(fail=True)).end(tenant, interaction.id, "test_chat")

    assert (interaction.status, interaction.outcome) == ("completed", "answered")
    assert "secret transcript text" not in caplog.text
    # The summarizer shares the session: its failure is rolled back after the close's commit.
    assert store.calls.index("rollback") > store.calls.index("checkpoint")


async def test_end_of_another_tenants_or_unknown_interaction_is_not_found():
    store, summarizer = MemoryStore(), FakeSummarizer()
    owner, other = _tenant(), _tenant()
    interaction = await _open(store, owner)
    lifecycle = _lifecycle(store, summarizer)

    with pytest.raises(InteractionNotFoundError):
        await lifecycle.end(other, interaction.id, "test_chat")
    with pytest.raises(InteractionNotFoundError):
        await lifecycle.end(owner, uuid.uuid4(), "test_chat")
    assert interaction.status == "in_progress" and summarizer.calls == []


async def test_abandon_idle_closes_only_this_businesses_idle_rows_without_ai():
    store, summarizer, tenant = MemoryStore(), FakeSummarizer(), _tenant()
    other = _tenant()
    idle = await _open(store, tenant)
    idle.updated_at = NOW - timedelta(minutes=16)
    idle_escalated = await _open(store, tenant, requires_human=True)
    idle_escalated.updated_at = NOW - timedelta(minutes=60)
    fresh = await _open(store, tenant)
    fresh.updated_at = NOW - timedelta(minutes=5)
    foreign = await _open(store, other)
    foreign.updated_at = NOW - timedelta(minutes=60)
    already_done = await _open(store, tenant)
    already_done.updated_at = NOW - timedelta(minutes=60)
    already_done.status = "completed"

    count = await _lifecycle(store, summarizer).abandon_idle(_bid(tenant))

    assert count == 2
    assert (idle.status, idle.outcome, idle.ended_at) == ("abandoned", "abandoned", idle.updated_at)
    assert (idle_escalated.status, idle_escalated.outcome) == ("abandoned", "escalated")
    assert fresh.status == "in_progress" and fresh.outcome is None
    assert foreign.status == "in_progress"
    assert already_done.status == "completed" and already_done.outcome is None
    assert sorted(c[1] for c in summarizer.calls) == sorted([idle.id, idle_escalated.id])
    assert all(ai is False for _, _, ai in summarizer.calls)  # zero AI calls


async def test_abandon_idle_is_bounded_per_call():
    store, summarizer, tenant = MemoryStore(), FakeSummarizer(), _tenant()
    for _ in range(53):
        row = await _open(store, tenant)
        row.updated_at = NOW - timedelta(hours=2)
    lifecycle = _lifecycle(store, summarizer)

    assert await lifecycle.abandon_idle(_bid(tenant)) == 50
    assert await lifecycle.abandon_idle(_bid(tenant)) == 3
    assert await lifecycle.abandon_idle(_bid(tenant)) == 0


async def test_sweep_idle_failure_is_logged_rolled_back_and_reported_as_zero(caplog):
    store, tenant = MemoryStore(), _tenant()

    async def broken(*args):
        raise RuntimeError("db said: secret text")

    store.lock_idle = broken  # type: ignore[method-assign]

    with caplog.at_level("WARNING"):
        swept = await _lifecycle(store, FakeSummarizer()).sweep_idle(_bid(tenant))

    assert swept == 0 and "rollback" in store.calls
    assert "secret text" not in caplog.text
