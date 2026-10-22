"""Session-bound store and dependencies for the services catalog, the weekly hours and
`services.directory.BusinessDirectory`.

`SqlDirectoryStore` binds the `repositories` functions to one session and speaks the
services' types (`services.hours.DayWindow`), which is why it lives in `core/` and not
in `repositories/`. One instance satisfies `services.directory.DirectoryStore`,
`services.catalog.CatalogStore` and `services.hours.HoursStore`.
"""

import uuid
from collections.abc import Sequence
from decimal import Decimal

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from models.hours import BusinessHours
from models.service import Service
from repositories import hours as hours_repo
from repositories import services as services_repo
from services.hours import DayWindow


class SqlDirectoryStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_services(
        self, business_id: uuid.UUID, *, active_only: bool
    ) -> Sequence[Service]:
        return await services_repo.list_services(
            self._session, business_id, active_only=active_only
        )

    async def get_service(self, business_id: uuid.UUID, service_id: uuid.UUID) -> Service | None:
        return await services_repo.get_service(self._session, business_id, service_id)

    async def lock_and_count_services(self, business_id: uuid.UUID) -> int:
        return await services_repo.lock_and_count_services(self._session, business_id)

    async def create_service(
        self,
        business_id: uuid.UUID,
        *,
        name: str,
        description: str | None,
        duration_minutes: int,
        price: Decimal | None,
        active: bool,
    ) -> Service:
        return await services_repo.create_service(
            self._session,
            business_id,
            name=name,
            description=description,
            duration_minutes=duration_minutes,
            price=price,
            active=active,
        )

    async def save_service(self, service: Service) -> Service:
        return await services_repo.save_service(self._session, service)

    async def delete_service(self, service: Service) -> None:
        await services_repo.delete_service(self._session, service)

    async def list_hours(self, business_id: uuid.UUID) -> Sequence[BusinessHours]:
        return await hours_repo.list_hours(self._session, business_id)

    async def replace_week(
        self, business_id: uuid.UUID, days: Sequence[DayWindow]
    ) -> Sequence[BusinessHours]:
        return await hours_repo.replace_week(
            self._session,
            business_id,
            [(d.day_of_week, d.is_closed, d.opens_at, d.closes_at) for d in days],
        )


def get_directory_store(session: AsyncSession = Depends(get_session)) -> SqlDirectoryStore:
    return SqlDirectoryStore(session)


def get_service_max() -> int:
    """The per-business service cap; a dependency so tests can lower it."""
    return settings.service_max_per_business
