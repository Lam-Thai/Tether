"""Integration tests against a real Postgres (DATABASE_URL from the env)."""

import uuid

from sqlalchemy import delete

from core.db import async_session_factory
from models.business import Business
from models.user import User
from repositories.business import (
    create_business,
    get_business_by_id_for_owner,
    get_business_by_owner_id,
    update_business,
)
from repositories.users import create_user


async def _create_owner(session) -> uuid.UUID:
    owner = await create_user(
        session, external_auth_id=f"user_{uuid.uuid4().hex}", email="owner@example.com"
    )
    await session.flush()
    return owner.id


async def test_get_business_by_owner_id_returns_none_when_missing():
    async with async_session_factory() as session:
        business = await get_business_by_owner_id(session, uuid.uuid4())

    assert business is None


async def test_create_then_get_business_by_owner_id_round_trips():
    async with async_session_factory() as session:
        try:
            owner_id = await _create_owner(session)
            created = await create_business(
                session,
                owner_id=owner_id,
                name="Alpha Salon",
                industry="Salon",
                address="123 Main St",
                timezone="America/New_York",
                phone=None,
                contact_email=None,
            )
            await session.commit()

            found = await get_business_by_owner_id(session, owner_id)

            assert found is not None
            assert found.id == created.id
            assert found.name == "Alpha Salon"
        finally:
            await session.execute(delete(Business).where(Business.owner_id == owner_id))
            await session.execute(delete(User).where(User.id == owner_id))
            await session.commit()


async def test_get_business_by_id_for_owner_returns_none_for_a_different_owner():
    async with async_session_factory() as session:
        try:
            owner_id = await _create_owner(session)
            other_owner_id = await _create_owner(session)
            business = await create_business(
                session,
                owner_id=owner_id,
                name="Alpha Salon",
                industry="Salon",
                address="123 Main St",
                timezone="America/New_York",
                phone=None,
                contact_email=None,
            )
            await session.commit()

            found_by_owner = await get_business_by_id_for_owner(session, business.id, owner_id)
            found_by_other = await get_business_by_id_for_owner(
                session, business.id, other_owner_id
            )

            assert found_by_owner is not None
            assert found_by_other is None
        finally:
            await session.execute(delete(Business).where(Business.owner_id == owner_id))
            await session.execute(
                delete(User).where(User.id.in_([owner_id, other_owner_id]))
            )
            await session.commit()


async def test_update_business_applies_only_passed_fields():
    async with async_session_factory() as session:
        try:
            owner_id = await _create_owner(session)
            business = await create_business(
                session,
                owner_id=owner_id,
                name="Alpha Salon",
                industry="Salon",
                address="123 Main St",
                timezone="America/New_York",
                phone=None,
                contact_email=None,
            )
            await session.commit()

            updated = await update_business(session, business, name="Alpha Salon & Spa")
            await session.commit()

            assert updated.name == "Alpha Salon & Spa"
            assert updated.industry == "Salon"  # untouched
        finally:
            await session.execute(delete(Business).where(Business.owner_id == owner_id))
            await session.execute(delete(User).where(User.id == owner_id))
            await session.commit()
