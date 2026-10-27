"""One alert per interaction and reopening, against real Postgres."""

import pytest
from sqlalchemy.exc import NoResultFound

from core.db import async_session_factory
from repositories import escalations as repo
from tests.api.inbox_factory import add_interaction


async def _record(business_id, interaction_id, reason, *, urgency="normal", sms="pending", **kw):
    async with async_session_factory() as session:
        row, outcome = await repo.insert_or_get(
            session, business_id, interaction_id, reason, urgency, sms, **kw
        )
        out = (row.id, row.sms_status, outcome, row.status)
        await session.commit()
    return out


async def _set_sms(business_id, escalation_id, status, error=None):
    async with async_session_factory() as session:
        assert await repo.claim_pending(session, business_id, escalation_id) or True
        await session.execute(
            repo.update(repo.Escalation)
            .where(repo.Escalation.id == escalation_id)
            .values(sms_status=status, sms_error=error)
        )
        await session.commit()


@pytest.mark.parametrize(
    ("first_sms", "first_urgency", "second", "expected"),
    [
        # SMS was off for the first one: the second still sends.
        ("disabled", "normal", ("urgent", "high"), "pending"),
        # The first alert failed: the second tries again.
        ("failed", "normal", ("human_requested", "normal"), "pending"),
        ("undelivered", "normal", ("human_requested", "normal"), "pending"),
        # A routine alert never mutes an urgent one.
        ("sent", "normal", ("urgent", "high"), "pending"),
        # An urgent alert out covers a routine one.
        ("sent", "high", ("complaint", "normal"), "skipped"),
        ("queued", "normal", ("complaint", "normal"), "skipped"),
        ("logged", "normal", ("complaint", "normal"), "skipped"),
        ("delivered", "high", ("urgent_two", "high"), "skipped"),
    ],
    ids=[
        "disabled-then-urgent",
        "failed-then-human",
        "undelivered-then-human",
        "sent-then-urgent",
        "urgent-sent-then-complaint",
        "queued-then-complaint",
        "logged-then-complaint",
        "urgent-delivered-then-urgent",
    ],
)
async def test_one_alert_per_interaction_rules(
    two_tenants, first_sms, first_urgency, second, expected
):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)
    first_reason = "urgent" if first_urgency == "high" else "change_request"
    first_id, _, _, _ = await _record(a, interaction, first_reason, urgency=first_urgency)
    await _set_sms(a, first_id, first_sms)
    reason, urgency = second
    if reason == "urgent_two":
        reason = "human_requested"  # a second high-urgency row (the reason set is fixed)

    _, sms_status, outcome, _ = await _record(a, interaction, reason, urgency=urgency)

    assert outcome == "created" and sms_status == expected


async def test_a_resolved_turn_reason_is_reopened_with_a_fresh_alert(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)
    esc_id, _, _, _ = await _record(a, interaction, "complaint")
    await _set_sms(a, esc_id, "sent")
    async with async_session_factory() as session:
        await repo.resolve_escalation(session, a, esc_id)
        await session.commit()

    again = await _record(a, interaction, "complaint", reopen=True)

    assert again == (esc_id, "pending", "reopened", "open")
    async with async_session_factory() as session:
        row = await repo.get_escalation(session, a, esc_id)
    assert row is not None
    assert (row.resolved_at, row.sms_sid, row.sms_error, row.notified_at) == (None,) * 4


async def test_reopen_respects_sms_off_and_earlier_alerts(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)
    first, _, _, _ = await _record(a, interaction, "complaint")
    second, _, _, _ = await _record(a, interaction, "human_requested")
    async with async_session_factory() as session:
        await repo.resolve_escalation(session, a, second)
        await session.commit()

    # Another alert (the complaint's, pending) is still out: no second SMS.
    reopened = await _record(a, interaction, "human_requested", reopen=True)
    assert reopened[1:] == ("skipped", "reopened", "open")
    # SMS off wins over everything for a row that would not send anyway.
    async with async_session_factory() as session:
        await repo.resolve_escalation(session, a, first)
        await session.commit()
    off = await _record(a, interaction, "complaint", sms="disabled", reopen=True)
    assert off[1:] == ("disabled", "reopened", "open")


async def test_without_reopen_a_resolved_row_stays_resolved(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    interaction = await add_interaction(a)
    esc_id, _, _, _ = await _record(a, interaction, "booking_unresolved")
    async with async_session_factory() as session:
        await repo.resolve_escalation(session, a, esc_id)
        await session.commit()

    again = await _record(a, interaction, "booking_unresolved", reopen=False)

    assert again == (esc_id, "pending", "existing", "resolved")


async def test_reopen_never_touches_another_business(two_tenants):
    tenant_a, tenant_b = two_tenants
    a, b = tenant_a.business_id, tenant_b.business_id
    interaction = await add_interaction(a)
    esc_id, _, _, _ = await _record(a, interaction, "complaint")
    async with async_session_factory() as session:
        await repo.resolve_escalation(session, a, esc_id)
        await session.commit()
    async with async_session_factory() as session:
        # B naming A's interaction id (the engine never does) reopens nothing of A's: the
        # conflicting insert is a no-op, the reopen UPDATE is scoped to B, and B owns no row.
        with pytest.raises(NoResultFound):
            await repo.insert_or_get(
                session, b, interaction, "complaint", "normal", "pending", reopen=True
            )
        await session.rollback()
        assert await repo.get_escalation(session, b, esc_id) is None
    async with async_session_factory() as session:
        row = await repo.get_escalation(session, a, esc_id)
    assert row is not None and row.status == "resolved"
