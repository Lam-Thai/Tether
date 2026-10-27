"""`repositories/phone_numbers.py` against real Postgres: uniqueness, scoping, constraints."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from core.db import async_session_factory
from integrations.twilio.fake import random_number_sid, random_phone_number
from models.interaction import Interaction
from models.phone_number import PhoneNumber
from repositories import interactions as interactions_repo
from repositories import phone_numbers as repo


async def _insert(business_id, *, phone=None, sid=None, status="connected"):
    phone = phone or random_phone_number()
    sid = sid or random_number_sid()
    async with async_session_factory() as session:
        row = await repo.insert_number(
            session,
            business_id=business_id,
            phone_number=phone,
            provider_number_sid=sid,
            status=status,
            last_error=None,
            verified_at=None,
        )
        out = None if row is None else (row.id, row.phone_number, row.provider_number_sid)
        await session.commit()
    return out, phone, sid


async def test_insert_returns_the_stored_row(two_tenants):
    tenant_a, _ = two_tenants

    inserted, phone, sid = await _insert(tenant_a.business_id)

    assert inserted is not None
    async with async_session_factory() as session:
        row = await repo.get(session, tenant_a.business_id, inserted[0])
    assert row is not None
    assert (row.phone_number, row.provider_number_sid, row.provider) == (phone, sid, "twilio")
    assert row.business_id == tenant_a.business_id


async def test_another_business_cannot_insert_the_same_phone_number(two_tenants):
    tenant_a, tenant_b = two_tenants
    first, phone, _ = await _insert(tenant_a.business_id)

    second, _, _ = await _insert(tenant_b.business_id, phone=phone)

    assert first is not None
    assert second is None
    async with async_session_factory() as session:
        assert await repo.list_for_business(session, tenant_b.business_id) == []


async def test_another_business_cannot_insert_the_same_provider_sid(two_tenants):
    tenant_a, tenant_b = two_tenants
    first, _, sid = await _insert(tenant_a.business_id)

    second, _, _ = await _insert(tenant_b.business_id, sid=sid)

    assert first is not None
    assert second is None
    async with async_session_factory() as session:
        assert await repo.list_for_business(session, tenant_b.business_id) == []


async def test_reads_and_deletes_are_scoped_to_the_business(two_tenants):
    tenant_a, tenant_b = two_tenants
    a_row, _, a_sid = await _insert(tenant_a.business_id)
    b_row, _, b_sid = await _insert(tenant_b.business_id)
    assert a_row is not None and b_row is not None

    async with async_session_factory() as session:
        assert [n.id for n in await repo.list_for_business(session, tenant_a.business_id)] == [
            a_row[0]
        ]
        assert await repo.get(session, tenant_a.business_id, b_row[0]) is None
        assert await repo.get_by_sid(session, tenant_a.business_id, b_sid) is None
        own = await repo.get_by_sid(session, tenant_a.business_id, a_sid)
        assert own is not None and own.id == a_row[0]

        assert await repo.delete_number(session, tenant_a.business_id, b_row[0]) is False
        await session.commit()

    async with async_session_factory() as session:
        assert await repo.get(session, tenant_b.business_id, b_row[0]) is not None
        assert await repo.delete_number(session, tenant_a.business_id, a_row[0]) is True
        assert await repo.delete_number(session, tenant_a.business_id, a_row[0]) is False
        await session.commit()
    async with async_session_factory() as session:
        assert await repo.get(session, tenant_a.business_id, a_row[0]) is None


async def test_find_by_number_finds_any_business_number(two_tenants):
    tenant_a, tenant_b = two_tenants
    _, a_phone, _ = await _insert(tenant_a.business_id)
    _, b_phone, _ = await _insert(tenant_b.business_id)

    async with async_session_factory() as session:
        a = await repo.find_by_number(session, a_phone)
        b = await repo.find_by_number(session, b_phone)
        missing = await repo.find_by_number(session, random_phone_number())

    assert a is not None and a.business_id == tenant_a.business_id
    assert b is not None and b.business_id == tenant_b.business_id
    assert missing is None


async def test_associated_sids_returns_only_held_sids_across_businesses(two_tenants):
    tenant_a, tenant_b = two_tenants
    _, _, a_sid = await _insert(tenant_a.business_id)
    _, _, b_sid = await _insert(tenant_b.business_id)
    free = random_number_sid()

    async with async_session_factory() as session:
        held = await repo.associated_sids(session, [a_sid, b_sid, free])
        none = await repo.associated_sids(session, [])

    assert held == {a_sid, b_sid}
    assert none == set()


@pytest.mark.parametrize(
    "bad",
    ["15551234567", "+0123456", "+1555", "+1555abc4567", "+1 555 123 4567"],
    ids=["no_plus", "leading_zero", "too_short", "letters", "spaces"],
)
async def test_check_constraint_rejects_a_non_e164_number(two_tenants, bad):
    tenant_a, _ = two_tenants

    async with async_session_factory() as session:
        session.add(
            PhoneNumber(
                id=uuid.uuid4(),
                business_id=tenant_a.business_id,
                provider="twilio",
                phone_number=bad,
                provider_number_sid=random_number_sid(),
                status="connected",
            )
        )
        with pytest.raises(IntegrityError) as caught:
            await session.commit()

    assert "ck_phone_numbers_phone_number" in str(caught.value)


async def test_deleting_a_number_keeps_its_calls_with_a_null_phone_number_id(two_tenants):
    tenant_a, _ = two_tenants
    row, _, _ = await _insert(tenant_a.business_id)
    assert row is not None
    call_sid = "CA" + uuid.uuid4().hex
    async with async_session_factory() as session:
        interaction, created = await interactions_repo.create_voice_call(
            session,
            business_id=tenant_a.business_id,
            phone_number_id=row[0],
            call_sid=call_sid,
            caller="+15557654321",
        )
        interaction_id = interaction.id
        await session.commit()
    assert created is True

    async with async_session_factory() as session:
        assert await repo.delete_number(session, tenant_a.business_id, row[0]) is True
        await session.commit()

    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT phone_number_id FROM interactions WHERE id = :id"),
            {"id": interaction_id},
        )
        assert result.one() == (None,)
        kept = await session.get(Interaction, interaction_id)
        assert kept is not None and kept.external_call_id == call_sid
