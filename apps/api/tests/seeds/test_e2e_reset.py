"""The E2E reset: deletes the user's business and its data, only on a local database."""

import uuid

import pytest
from sqlalchemy import func, select

import seeds.e2e_reset as e2e_reset
from core.db import async_session_factory
from models.business import Business
from models.user import User
from seeds.e2e_reset import refuse_unless_safe, reset_user


async def test_reset_deletes_the_business_of_that_email_only(entitled_tenants):
    tenant, other = entitled_tenants
    async with async_session_factory() as session:
        deleted = await reset_user(session, tenant.email.upper())
        await session.commit()

    assert deleted == 1
    async with async_session_factory() as session:
        remaining = set(
            (
                await session.execute(
                    select(Business.id).where(
                        Business.id.in_([tenant.business_id, other.business_id])
                    )
                )
            ).scalars()
        )
        users = await session.scalar(
            select(func.count()).select_from(User).where(User.email == tenant.email)
        )
    assert remaining == {other.business_id}
    assert users == 1  # the sign-in row stays, so the same login onboards again


async def test_reset_of_an_unknown_email_deletes_nothing():
    async with async_session_factory() as session:
        assert await reset_user(session, f"nobody-{uuid.uuid4().hex}@example.com") == 0


def test_reset_refuses_production(monkeypatch):
    monkeypatch.setattr(e2e_reset.settings, "environment", "production")
    with pytest.raises(SystemExit, match="production"):
        refuse_unless_safe()


def test_reset_refuses_a_remote_database(monkeypatch):
    monkeypatch.setattr(
        e2e_reset.settings, "database_url", "postgresql://u:p@db.example.com:5432/tether"
    )
    with pytest.raises(SystemExit, match="not local"):
        refuse_unless_safe()


async def test_run_checks_safety_before_touching_the_database(monkeypatch):
    monkeypatch.setattr(e2e_reset.settings, "environment", "production")
    with pytest.raises(SystemExit):
        await e2e_reset.run("someone@example.com")
