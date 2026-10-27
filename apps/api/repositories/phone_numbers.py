"""Repository for `phone_numbers`.

Owner-facing reads and writes are scoped by `business_id` (from `TenantContext`). Two
lookups are deliberately NOT tenant-scoped, because there is no tenant yet: the inbound
call webhook resolves the business FROM the number (`find_by_number`), and the owner's
list has to know which account numbers some business already holds
(`associated_sids`, which returns only sids, never who holds them).
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.phone_number import PhoneNumber
from repositories.base import business_scoped_select


async def list_for_business(session: AsyncSession, business_id: uuid.UUID) -> Sequence[PhoneNumber]:
    result = await session.execute(
        business_scoped_select(PhoneNumber, business_id).order_by(
            PhoneNumber.created_at, PhoneNumber.id
        )
    )
    return result.scalars().all()


async def get(
    session: AsyncSession, business_id: uuid.UUID, number_id: uuid.UUID
) -> PhoneNumber | None:
    result = await session.execute(
        business_scoped_select(PhoneNumber, business_id).where(PhoneNumber.id == number_id)
    )
    return result.scalar_one_or_none()


async def get_by_sid(
    session: AsyncSession, business_id: uuid.UUID, provider_number_sid: str
) -> PhoneNumber | None:
    result = await session.execute(
        business_scoped_select(PhoneNumber, business_id).where(
            PhoneNumber.provider_number_sid == provider_number_sid
        )
    )
    return result.scalar_one_or_none()


async def associated_sids(session: AsyncSession, sids: Sequence[str]) -> set[str]:
    """Which of these account numbers ANY business holds (not scoped; sids only)."""
    if not sids:
        return set()
    result = await session.execute(
        select(PhoneNumber.provider_number_sid).where(PhoneNumber.provider_number_sid.in_(sids))
    )
    return set(result.scalars().all())


async def find_by_number(session: AsyncSession, phone_number: str) -> PhoneNumber | None:
    """The number an inbound call came in on (not scoped: the row decides the business)."""
    result = await session.execute(
        select(PhoneNumber).where(PhoneNumber.phone_number == phone_number)
    )
    return result.scalar_one_or_none()


async def insert_number(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    phone_number: str,
    provider_number_sid: str,
    status: str,
    last_error: str | None,
    verified_at: datetime | None,
) -> PhoneNumber | None:
    """Inserts the association, or returns None when the number (or its sid) is already
    held: the UNIQUE constraints decide a race between two businesses."""
    result = await session.execute(
        insert(PhoneNumber)
        .values(
            id=uuid.uuid4(),
            business_id=business_id,
            provider="twilio",
            phone_number=phone_number,
            provider_number_sid=provider_number_sid,
            status=status,
            last_error=last_error,
            verified_at=verified_at,
        )
        .on_conflict_do_nothing()
        .returning(PhoneNumber.id)
    )
    new_id = result.scalar_one_or_none()
    if new_id is None:
        return None
    return await get(session, business_id, new_id)


async def delete_number(
    session: AsyncSession, business_id: uuid.UUID, number_id: uuid.UUID
) -> bool:
    result = await session.execute(
        delete(PhoneNumber).where(
            PhoneNumber.business_id == business_id, PhoneNumber.id == number_id
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


class SqlPhoneNumberStore:
    """Binds the functions above to one session (`services.phone_numbers.PhoneNumberStore`)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_business(self, business_id: uuid.UUID) -> Sequence[PhoneNumber]:
        return await list_for_business(self._session, business_id)

    async def get(self, business_id: uuid.UUID, number_id: uuid.UUID) -> PhoneNumber | None:
        return await get(self._session, business_id, number_id)

    async def get_by_sid(
        self, business_id: uuid.UUID, provider_number_sid: str
    ) -> PhoneNumber | None:
        return await get_by_sid(self._session, business_id, provider_number_sid)

    async def associated_sids(self, sids: Sequence[str]) -> set[str]:
        return await associated_sids(self._session, sids)

    async def insert(
        self,
        *,
        business_id: uuid.UUID,
        phone_number: str,
        provider_number_sid: str,
        status: str,
        last_error: str | None,
        verified_at: datetime | None,
    ) -> PhoneNumber | None:
        return await insert_number(
            self._session,
            business_id=business_id,
            phone_number=phone_number,
            provider_number_sid=provider_number_sid,
            status=status,
            last_error=last_error,
            verified_at=verified_at,
        )

    async def save(self, number: PhoneNumber) -> PhoneNumber:
        await self._session.flush()
        await self._session.refresh(number)
        return number

    async def delete(self, business_id: uuid.UUID, number_id: uuid.UUID) -> bool:
        return await delete_number(self._session, business_id, number_id)

    async def checkpoint(self) -> None:
        await self._session.commit()
