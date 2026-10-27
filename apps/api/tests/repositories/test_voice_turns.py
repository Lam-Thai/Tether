"""`repositories/voice_turns.py` and the voice-call helpers of `repositories/interactions.py`
against real Postgres. Each operation commits in its own session, like `SqlTurnClaims`."""

import asyncio
import uuid

from sqlalchemy import text

from core.db import async_session_factory
from integrations.twilio.fake import random_number_sid, random_phone_number
from repositories import interactions as interactions_repo
from repositories import phone_numbers as numbers_repo
from repositories import voice_turns as repo
from tests.api.inbox_factory import add_interaction

STALE_NEVER = 3600.0


async def _number(business_id) -> uuid.UUID:
    async with async_session_factory() as session:
        row = await numbers_repo.insert_number(
            session,
            business_id=business_id,
            phone_number=random_phone_number(),
            provider_number_sid=random_number_sid(),
            status="connected",
            last_error=None,
            verified_at=None,
        )
        assert row is not None
        number_id = row.id
        await session.commit()
    return number_id


async def _call(business_id, call_sid=None):
    number_id = await _number(business_id)
    call_sid = call_sid or "CA" + uuid.uuid4().hex
    async with async_session_factory() as session:
        interaction, created = await interactions_repo.create_voice_call(
            session,
            business_id=business_id,
            phone_number_id=number_id,
            call_sid=call_sid,
            caller="+15557654321",
        )
        interaction_id = interaction.id
        await session.commit()
    return interaction_id, call_sid, number_id, created


async def _claim(business_id, interaction_id, turn=1, claim=None, utterance="hello"):
    claim = claim or uuid.uuid4()
    async with async_session_factory() as session:
        inserted = await repo.insert_claim(
            session,
            business_id=business_id,
            interaction_id=interaction_id,
            turn=turn,
            utterance=utterance,
            claim=claim,
        )
        await session.commit()
    return inserted, claim


async def _turn(business_id, interaction_id, turn=1):
    async with async_session_factory() as session:
        return await repo.get_turn(session, business_id, interaction_id, turn)


async def _backdate(turn_id, seconds=60):
    async with async_session_factory() as session:
        await session.execute(
            text(
                "UPDATE voice_turns SET heartbeat_at = now() - make_interval(secs => :s) "
                "WHERE id = :id"
            ),
            {"s": seconds, "id": turn_id},
        )
        await session.commit()


async def _take_over(turn_id, seen, new, stale_after):
    async with async_session_factory() as session:
        won = await repo.take_over(session, turn_id, seen, new, stale_after)
        await session.commit()
    return won


async def _heartbeat(turn_id, claim) -> bool:
    async with async_session_factory() as session:
        ok = await repo.heartbeat(session, turn_id, claim)
        await session.commit()
    return ok


async def _finish(turn_id, claim, status):
    async with async_session_factory() as session:
        await repo.finish(session, turn_id, claim, status)
        await session.commit()


# --- voice calls ----------------------------------------------------------------------


async def test_create_voice_call_twice_returns_the_same_interaction(two_tenants):
    tenant_a, _ = two_tenants
    number_id = await _number(tenant_a.business_id)
    call_sid = "CA" + uuid.uuid4().hex

    async def create():
        async with async_session_factory() as session:
            interaction, created = await interactions_repo.create_voice_call(
                session,
                business_id=tenant_a.business_id,
                phone_number_id=number_id,
                call_sid=call_sid,
                caller="+15557654321",
            )
            out = (interaction.id, created, interaction.channel, interaction.status)
            await session.commit()
        return out

    first = await create()
    second = await create()

    assert first[1] is True
    assert second[1] is False
    assert second[0] == first[0]
    assert (first[2], first[3]) == ("voice", "in_progress")
    async with async_session_factory() as session:
        found = await interactions_repo.find_voice_call(session, call_sid)
        assert found is not None
        assert (found.id, found.business_id, found.phone_number_id) == (
            first[0],
            tenant_a.business_id,
            number_id,
        )
        assert found.caller_identifier == "+15557654321"
        assert await interactions_repo.find_voice_call(session, "CA" + uuid.uuid4().hex) is None


async def test_concurrent_duplicate_call_webhooks_create_one_interaction(two_tenants):
    tenant_a, _ = two_tenants
    number_id = await _number(tenant_a.business_id)
    call_sid = "CA" + uuid.uuid4().hex

    async def create():
        async with async_session_factory() as session:
            interaction, created = await interactions_repo.create_voice_call(
                session,
                business_id=tenant_a.business_id,
                phone_number_id=number_id,
                call_sid=call_sid,
                caller=None,
            )
            out = (interaction.id, created)
            await session.commit()
        return out

    results = await asyncio.gather(*(create() for _ in range(6)))

    assert len({r[0] for r in results}) == 1
    assert sum(1 for r in results if r[1]) == 1


async def test_the_unique_index_is_voice_only(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, call_sid, _, _ = await _call(tenant_a.business_id)

    chat_id = await add_interaction(tenant_a.business_id, channel="test_chat")
    async with async_session_factory() as session:
        await session.execute(
            text("UPDATE interactions SET external_call_id = :sid WHERE id = :id"),
            {"sid": call_sid, "id": chat_id},
        )
        await session.commit()

    async with async_session_factory() as session:
        found = await interactions_repo.find_voice_call(session, call_sid)
    assert found is not None and found.id == interaction_id


# --- voice turns ----------------------------------------------------------------------


async def test_insert_claim_is_false_for_a_duplicate_turn(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)

    first, claim = await _claim(tenant_a.business_id, interaction_id, turn=1)
    dup, _ = await _claim(tenant_a.business_id, interaction_id, turn=1)
    next_turn, _ = await _claim(tenant_a.business_id, interaction_id, turn=2)

    assert (first, dup, next_turn) == (True, False, True)
    row = await _turn(tenant_a.business_id, interaction_id, 1)
    assert row is not None
    assert (row.claim, row.status, row.utterance) == (claim, "pending", "hello")


async def test_get_turn_and_max_turn_are_scoped_to_the_business(two_tenants):
    tenant_a, tenant_b = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)
    await _claim(tenant_a.business_id, interaction_id, turn=1)
    await _claim(tenant_a.business_id, interaction_id, turn=3)

    assert await _turn(tenant_b.business_id, interaction_id, 1) is None
    async with async_session_factory() as session:
        assert await repo.max_turn(session, tenant_a.business_id, interaction_id) == 3
        assert await repo.max_turn(session, tenant_b.business_id, interaction_id) == 0
        assert await repo.max_turn(session, tenant_a.business_id, uuid.uuid4()) == 0


async def test_heartbeat_is_true_for_the_owner_and_false_for_a_wrong_claim(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)
    _, claim = await _claim(tenant_a.business_id, interaction_id)
    row = await _turn(tenant_a.business_id, interaction_id)
    assert row is not None
    await _backdate(row.id)

    assert await _heartbeat(row.id, uuid.uuid4()) is False
    stale = await _turn(tenant_a.business_id, interaction_id)
    assert stale is not None

    assert await _heartbeat(row.id, claim) is True
    fresh = await _turn(tenant_a.business_id, interaction_id)
    assert fresh is not None and fresh.heartbeat_at > stale.heartbeat_at


async def test_take_over_is_refused_while_the_heartbeat_is_fresh(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)
    _, claim = await _claim(tenant_a.business_id, interaction_id)
    row = await _turn(tenant_a.business_id, interaction_id)
    assert row is not None

    assert await _take_over(row.id, claim, uuid.uuid4(), STALE_NEVER) is False

    after = await _turn(tenant_a.business_id, interaction_id)
    assert after is not None and after.claim == claim


async def test_take_over_succeeds_once_stale_and_then_the_old_owner_is_locked_out(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)
    _, claim = await _claim(tenant_a.business_id, interaction_id)
    row = await _turn(tenant_a.business_id, interaction_id)
    assert row is not None
    await _backdate(row.id, seconds=120)
    new = uuid.uuid4()

    assert await _take_over(row.id, claim, new, 30.0) is True

    after = await _turn(tenant_a.business_id, interaction_id)
    assert after is not None and after.claim == new
    assert await _heartbeat(row.id, claim) is False
    assert await _heartbeat(row.id, new) is True
    # The fresh heartbeat of the new owner protects it from a further takeover.
    assert await _take_over(row.id, new, uuid.uuid4(), 30.0) is False


async def test_take_over_with_a_stale_view_of_the_claim_is_refused(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)
    await _claim(tenant_a.business_id, interaction_id)
    row = await _turn(tenant_a.business_id, interaction_id)
    assert row is not None
    await _backdate(row.id)

    assert await _take_over(row.id, uuid.uuid4(), uuid.uuid4(), 0.0) is False


async def test_only_one_of_two_concurrent_takers_wins(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)
    _, claim = await _claim(tenant_a.business_id, interaction_id)
    row = await _turn(tenant_a.business_id, interaction_id)
    assert row is not None
    await _backdate(row.id)
    taker_one, taker_two = uuid.uuid4(), uuid.uuid4()

    results = await asyncio.gather(
        _take_over(row.id, claim, taker_one, 0.0), _take_over(row.id, claim, taker_two, 0.0)
    )

    assert sorted(results) == [False, True]
    after = await _turn(tenant_a.business_id, interaction_id)
    assert after is not None
    assert after.claim == (taker_one if results[0] else taker_two)


async def test_finish_failed_only_by_the_owner(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)
    _, claim = await _claim(tenant_a.business_id, interaction_id)
    row = await _turn(tenant_a.business_id, interaction_id)
    assert row is not None

    await _finish(row.id, uuid.uuid4(), "failed")
    untouched = await _turn(tenant_a.business_id, interaction_id)
    assert untouched is not None
    assert (untouched.status, untouched.utterance) == ("pending", "hello")

    await _finish(row.id, claim, "failed")
    failed = await _turn(tenant_a.business_id, interaction_id)
    assert failed is not None
    assert (failed.status, failed.utterance) == ("failed", None)


async def test_finish_answered_by_anyone_clears_the_utterance(two_tenants):
    tenant_a, _ = two_tenants
    interaction_id, *_ = await _call(tenant_a.business_id)
    await _claim(tenant_a.business_id, interaction_id)
    row = await _turn(tenant_a.business_id, interaction_id)
    assert row is not None

    await _finish(row.id, uuid.uuid4(), "answered")

    done = await _turn(tenant_a.business_id, interaction_id)
    assert done is not None
    assert (done.status, done.utterance) == ("answered", None)
    # An answered turn can no longer be heart-beaten or taken over.
    assert await _heartbeat(row.id, done.claim) is False
    await _backdate(row.id)
    assert await _take_over(row.id, done.claim, uuid.uuid4(), 0.0) is False
