"""Escalation repository against real Postgres: constraints, scoping, races."""

import asyncio
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import async_session_factory
from models.escalation import Escalation
from repositories import escalations as repo
from tests.api.inbox_factory import add_interaction


@dataclass(frozen=True)
class _Settings:
    contact_phone: str | None = "+15551234567"
    sms_enabled: bool = True
    clarification_threshold: int = 2
    urgent_keywords: tuple[str, ...] = ()


async def _insert(
    business_id,
    interaction_id,
    reason="human_requested",
    sms="pending",
    urgency="normal",
    reopen=False,
):
    async with async_session_factory() as session:
        row, outcome = await repo.insert_or_get(
            session, business_id, interaction_id, reason, urgency, sms, reopen
        )
        out = (row.id, row.sms_status, outcome == "created")
        await session.commit()
    return out


async def test_insert_or_get_is_once_per_reason_and_skips_later_reasons(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)

    first = await _insert(a, interaction)
    again = await _insert(a, interaction)
    other = await _insert(a, interaction, reason="complaint")

    assert first[2] is True and first[1] == "pending"
    assert again == (first[0], "pending", False)
    assert other[2] is True and other[1] == "skipped"


async def test_concurrent_duplicate_insert_yields_one_row(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)

    results = await asyncio.gather(*(_insert(a, interaction) for _ in range(8)))

    assert len({r[0] for r in results}) == 1
    assert sum(1 for r in results if r[2]) == 1
    async with async_session_factory() as session:
        rows = await repo.list_escalations(session, a, status=None, interaction_id=None, limit=50)
    assert len(rows) == 1


async def test_concurrent_different_reasons_alert_only_one(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)

    results = await asyncio.gather(
        *(_insert(a, interaction, reason=r) for r in ("complaint", "human_requested", "urgent"))
    )

    assert sorted(r[1] for r in results) == ["pending", "skipped", "skipped"]


async def test_reads_and_writes_are_scoped_to_the_business(two_tenants):
    tenant_a, tenant_b = two_tenants
    a, b = tenant_a.business_id, tenant_b.business_id
    interaction = await add_interaction(a)
    esc_id, _, _ = await _insert(a, interaction)

    async with async_session_factory() as session:
        assert await repo.get_escalation(session, b, esc_id) is None
        assert await repo.resolve_escalation(session, b, esc_id) is None
        assert not await repo.claim_pending(session, b, esc_id)
        assert (
            await repo.list_escalations(session, b, status=None, interaction_id=None, limit=10)
            == []
        )
        assert await repo.list_for_interactions(session, b, [interaction]) == []
        mine = await repo.get_escalation(session, a, esc_id)
    assert mine is not None and mine.status == "open"


async def test_claim_is_conditional_and_resolve_is_idempotent(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)
    esc_id, _, _ = await _insert(a, interaction)

    async with async_session_factory() as session:
        assert await repo.claim_pending(session, a, esc_id) is True
        assert await repo.claim_pending(session, a, esc_id) is False
        done = await repo.finish_send(
            session,
            a,
            esc_id,
            sms_status="queued",
            sms_sid=f"SM{uuid.uuid4().hex}",
            sms_error=None,
            notified_at=None,
        )
        assert done is not None and done.sms_status == "queued"
        first = await repo.resolve_escalation(session, a, esc_id)
        second = await repo.resolve_escalation(session, a, esc_id)
        await session.commit()
    assert first is not None and second is not None
    assert first.status == "resolved" and first.resolved_at == second.resolved_at


async def test_sms_status_by_sid_is_conditional(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    sid = f"SM{uuid.uuid4().hex}"
    interaction = await add_interaction(a)
    esc_id, _, _ = await _insert(a, interaction)
    async with async_session_factory() as session:
        await repo.claim_pending(session, a, esc_id)
        await repo.finish_send(
            session, a, esc_id, sms_status="queued", sms_sid=sid, sms_error=None, notified_at=None
        )
        await session.commit()

    async with async_session_factory() as session:
        found = await repo.get_by_sid(session, sid)
        assert found is not None and found.id == esc_id
        assert await repo.get_by_sid(session, "SM_unknown") is None
        assert await repo.advance_sms_status(
            session, sid, seen_status="queued", sms_status="sent", sms_error=None
        )
        # A stale read loses: the row is no longer `queued`.
        assert not await repo.advance_sms_status(
            session, sid, seen_status="queued", sms_status="delivered", sms_error=None
        )
        await session.commit()


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "bogus"},
        {"urgency": "low"},
        {"status": "done"},
        {"sms_status": "weird"},
    ],
    ids=["reason", "urgency", "status", "sms-status"],
)
async def test_check_constraints(two_tenants, changes):
    tenant_a, _ = two_tenants
    interaction = await add_interaction(tenant_a.business_id)
    values = {
        "business_id": tenant_a.business_id,
        "interaction_id": interaction,
        "reason": "complaint",
        "urgency": "normal",
        "sms_status": "pending",
    } | changes
    async with async_session_factory() as session:
        session.add(Escalation(id=uuid.uuid4(), **values))
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_unique_sms_sid_and_unique_reason(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)
    sid = f"SM{uuid.uuid4().hex}"

    def row(reason: str, sms_sid: str | None) -> Escalation:
        return Escalation(
            id=uuid.uuid4(),
            business_id=a,
            interaction_id=interaction,
            reason=reason,
            urgency="normal",
            sms_status="pending",
            sms_sid=sms_sid,
        )

    async with async_session_factory() as session:
        session.add(row("complaint", sid))
        await session.flush()
        session.add(row("urgent", sid))
        with pytest.raises(IntegrityError):
            await session.flush()
    async with async_session_factory() as session:
        session.add(row("complaint", None))
        await session.commit()
    async with async_session_factory() as session:
        session.add(row("complaint", None))
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.parametrize(
    "bad",
    [
        _Settings(contact_phone="5551234567"),
        _Settings(contact_phone="+0123456"),
        _Settings(clarification_threshold=0),
        _Settings(clarification_threshold=6),
        _Settings(urgent_keywords=tuple(f"k{i}" for i in range(21))),
    ],
    ids=["no-plus", "leading-zero", "threshold-0", "threshold-6", "21-keywords"],
)
async def test_settings_constraints(two_tenants, bad):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        with pytest.raises(IntegrityError):
            await repo.upsert_settings(session, tenant_a.business_id, bad)


async def test_settings_upsert_and_scoping(two_tenants):
    tenant_a, tenant_b = two_tenants
    a, b = tenant_a.business_id, tenant_b.business_id
    async with async_session_factory() as session:
        assert await repo.get_settings(session, a) is None
        await repo.upsert_settings(session, a, _Settings(urgent_keywords=("fire",)))
        await session.commit()
    async with async_session_factory() as session:
        await repo.upsert_settings(session, a, _Settings(clarification_threshold=4))
        await session.commit()
    async with async_session_factory() as session:
        mine = await repo.get_settings(session, a)
        assert await repo.get_settings(session, b) is None
    assert mine is not None
    assert (mine.clarification_threshold, mine.urgent_keywords) == (4, [])
