"""Integration tests against a real Postgres (DATABASE_URL from the env)."""

import uuid

from sqlalchemy import delete

from core.db import async_session_factory
from models.user import User
from repositories.users import create_user, get_or_create_user, get_user_by_external_auth_id


async def test_get_user_by_external_auth_id_returns_none_when_missing():
    async with async_session_factory() as session:
        user = await get_user_by_external_auth_id(session, f"missing_{uuid.uuid4().hex}")

    assert user is None


async def test_create_user_then_lookup_round_trips():
    external_auth_id = f"user_{uuid.uuid4().hex}"
    async with async_session_factory() as session:
        try:
            created = await create_user(
                session, external_auth_id=external_auth_id, email="owner@example.com"
            )
            await session.commit()

            found = await get_user_by_external_auth_id(session, external_auth_id)

            assert found is not None
            assert found.id == created.id
            assert found.email == "owner@example.com"
        finally:
            await session.execute(delete(User).where(User.external_auth_id == external_auth_id))
            await session.commit()


async def test_get_or_create_user_is_idempotent():
    external_auth_id = f"user_{uuid.uuid4().hex}"
    async with async_session_factory() as session:
        try:
            first = await get_or_create_user(
                session, external_auth_id=external_auth_id, email="owner@example.com"
            )
            second = await get_or_create_user(
                session, external_auth_id=external_auth_id, email="owner@example.com"
            )
            await session.commit()

            assert first.id == second.id
        finally:
            await session.execute(delete(User).where(User.external_auth_id == external_auth_id))
            await session.commit()
