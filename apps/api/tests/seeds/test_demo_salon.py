"""The demo-salon seed against real Postgres, read back through `BusinessDirectory`.

Runs the seed's own functions on a test tenant's business (never the synthetic
seed owner), so it doubles as the directory's integration test over
`SqlDirectoryStore`: the issue's demo questions resolve from seeded rows.
"""

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import async_session_factory
from core.directory import SqlDirectoryStore
from core.tenant import Tenant
from models.business import Business
from models.user import User
from seeds.demo_salon import (
    DEMO_SALON_FAQS,
    DEMO_SALON_SERVICES,
    add_faqs,
    add_services,
    set_hours_if_missing,
)
from services.directory import Ambiguous, BusinessDirectory, Found, NotFound, Open


async def _tenant(session: AsyncSession, business_id: uuid.UUID) -> Tenant:
    business = (
        await session.execute(select(Business).where(Business.id == business_id))
    ).scalar_one()
    user = (await session.execute(select(User).where(User.id == business.owner_id))).scalar_one()
    return Tenant(user=user, business=business)


async def test_seed_is_idempotent_and_answers_the_demo_questions(two_tenants):
    tenant_a, tenant_b = two_tenants
    async with async_session_factory() as session:
        tenant = await _tenant(session, tenant_a.business_id)
        business = tenant.business
        assert business is not None

        first = (
            await add_faqs(session, business),
            await add_services(session, business),
            await set_hours_if_missing(session, business),
        )
        second = (
            await add_faqs(session, business),
            await add_services(session, business),
            await set_hours_if_missing(session, business),
        )
        await session.commit()

        assert first == ((len(DEMO_SALON_FAQS), 0), (len(DEMO_SALON_SERVICES), 0), True)
        assert second == ((0, len(DEMO_SALON_FAQS)), (0, len(DEMO_SALON_SERVICES)), False)

        directory = BusinessDirectory(
            SqlDirectoryStore(session), lambda: datetime(2026, 10, 21, 15, tzinfo=UTC)
        )

        beard = await directory.find_service(tenant, "beard trim")
        assert isinstance(beard, Found) and beard.service.price is not None
        assert isinstance(await directory.find_service(tenant, "perm"), NotFound)
        cut = await directory.find_service(tenant, "cut")
        assert isinstance(cut, Ambiguous)
        assert {"Haircut", "Kids cut"} <= {c.name for c in cut.candidates}
        assert all(s.active for s in await directory.services(tenant))

        saturday = await directory.opening_window(tenant, date(2026, 10, 24))
        assert isinstance(saturday, Open) and saturday.start.time() == time(10)

        other = await _tenant(session, tenant_b.business_id)
        assert await directory.services(other) == []
        assert await directory.weekly_hours(other) == []
