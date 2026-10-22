"""Services repository against real Postgres: ordering, scoping, unique names, cap lock."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import async_session_factory
from core.errors import ServiceNameTakenError
from repositories import services as repo


async def _create(session, business_id, name, **overrides):
    values = {
        "name": name,
        "description": None,
        "duration_minutes": 30,
        "price": None,
        "active": True,
    } | overrides
    return await repo.create_service(session, business_id, **values)


async def test_lists_orders_scopes_and_enforces_unique_names(two_tenants):
    tenant_a, tenant_b = two_tenants
    a, b = tenant_a.business_id, tenant_b.business_id
    async with async_session_factory() as session:
        haircut = await _create(session, a, "haircut", price=Decimal("40.5"))
        await _create(session, a, "Beard trim", active=False)
        await _create(session, a, "Colour")
        await _create(session, b, "Haircut")  # same name, other tenant: fine
        await session.commit()

    async with async_session_factory() as session:
        everything = await repo.list_services(session, a)
        active = await repo.list_services(session, a, active_only=True)
        foreign_get = await repo.get_service(session, b, haircut.id)
        own_get = await repo.get_service(session, a, haircut.id)
        counts = (
            await repo.count_services(session, a),
            await repo.lock_and_count_services(session, b),
        )
    assert [s.name for s in everything] == ["Beard trim", "Colour", "haircut"]
    assert [s.name for s in active] == ["Colour", "haircut"]
    assert foreign_get is None and own_get is not None and own_get.price == Decimal("40.50")
    assert counts == (3, 1)

    async with async_session_factory() as session:
        with pytest.raises(ServiceNameTakenError):
            await _create(session, a, "HAIRCUT")
        # The session is still usable after the violation (savepoint), and rename collides too.
        colour = next(s for s in await repo.list_services(session, a) if s.name == "Colour")
        colour.name = "haircut"
        with pytest.raises(ServiceNameTakenError):
            await repo.save_service(session, colour)
        await session.rollback()

    async with async_session_factory() as session:
        beard = next(s for s in await repo.list_services(session, a) if s.name == "Beard trim")
        beard.name = "Beard Trim & Shape"
        beard.price = Decimal("12")
        saved = await repo.save_service(session, beard)
        assert saved.updated_at >= saved.created_at
        await repo.delete_service(session, saved)
        await session.commit()
        assert await repo.count_services(session, a) == 2


async def test_check_constraints_reject_bad_rows(two_tenants):
    tenant_a, _ = two_tenants
    for overrides in ({"duration_minutes": 0}, {"price": Decimal("-1")}):
        async with async_session_factory() as session:
            with pytest.raises(IntegrityError):
                await _create(session, tenant_a.business_id, "Bad", **overrides)
