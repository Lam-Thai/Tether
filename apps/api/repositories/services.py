"""Repository for `services` (the catalog). Every query is scoped by `business_id`.

Names are unique per business ignoring case (`uq_services_business_lower_name`);
a write that trips it raises `ServiceNameTakenError` (create: the insert runs in a
savepoint, so the session stays usable; rename: see `save_service`).
"""

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ServiceNameTakenError
from models.service import Service
from repositories.base import business_scoped_select

NAME_INDEX = "uq_services_business_lower_name"


async def list_services(
    session: AsyncSession, business_id: uuid.UUID, *, active_only: bool = False
) -> Sequence[Service]:
    """Ordered by name, case-insensitively (then id, for a stable order)."""
    stmt = business_scoped_select(Service, business_id)
    if active_only:
        stmt = stmt.where(Service.active)
    result = await session.execute(stmt.order_by(func.lower(Service.name), Service.id))
    return result.scalars().all()


async def get_service(
    session: AsyncSession, business_id: uuid.UUID, service_id: uuid.UUID
) -> Service | None:
    result = await session.execute(
        business_scoped_select(Service, business_id).where(Service.id == service_id)
    )
    return result.scalar_one_or_none()


async def count_services(session: AsyncSession, business_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(Service).where(Service.business_id == business_id)
    )
    return result.scalar_one()


async def lock_and_count_services(session: AsyncSession, business_id: uuid.UUID) -> int:
    """Serializes service creation per business (transaction-level advisory lock),
    then counts every service, so the cap holds under concurrent creates."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('services:' || CAST(:b AS text), 0))"),
        {"b": str(business_id)},
    )
    return await count_services(session, business_id)


def _name_taken() -> ServiceNameTakenError:
    return ServiceNameTakenError("You already have a service with this name.")


async def create_service(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    name: str,
    description: str | None,
    duration_minutes: int,
    price: Decimal | None,
    active: bool,
) -> Service:
    service = Service(
        business_id=business_id,
        name=name,
        description=description,
        duration_minutes=duration_minutes,
        price=price,
        active=active,
    )
    try:
        # `begin_nested` flushes pending work on entry, so the insert goes *inside* the
        # savepoint (added here) and a name clash leaves the session usable.
        async with session.begin_nested():
            session.add(service)
    except IntegrityError as exc:
        if NAME_INDEX in str(exc.orig):
            raise _name_taken() from exc
        raise
    await session.refresh(service)
    return service


async def save_service(session: AsyncSession, service: Service) -> Service:
    """Flushes edits, then reloads server-computed columns (`updated_at`).

    A rename that clashes with another service is caught by a pre-check (without
    autoflush, so the session is untouched); the unique index stays the backstop for a
    concurrent rename, which fails the flush and so ends the request's transaction."""
    with session.no_autoflush:
        clash = await session.execute(
            select(Service.id).where(
                Service.business_id == service.business_id,
                func.lower(Service.name) == service.name.lower(),
                Service.id != service.id,
            )
        )
    if clash.first() is not None:
        raise _name_taken()
    try:
        await session.flush()
    except IntegrityError as exc:
        if NAME_INDEX in str(exc.orig):
            raise _name_taken() from exc
        raise
    await session.refresh(service)
    return service


async def delete_service(session: AsyncSession, service: Service) -> None:
    await session.delete(service)
    await session.flush()
