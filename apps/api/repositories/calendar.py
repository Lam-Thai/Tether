"""Repository for `calendar_connections`. Every query is scoped by `business_id`
resolved from `TenantContext`; there is at most one row per business.

Updates are conditional on the `updated_at` the caller read (`seen_updated_at`), so a
write based on a stale read (a reconnect or disconnect happened in between) changes
nothing and returns the current row instead. `updated_at` is set from
`clock_timestamp()`, so it differs on every write even within one transaction.

Writes are single statements with `RETURNING` and `populate_existing`, so the
row handed back always reflects the database, never a stale identity-map copy.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.calendar import CalendarConnection
from repositories.base import business_scoped_select

NEEDS_SELECTION = "needs_selection"
CONNECTED = "connected"


async def get_connection(
    session: AsyncSession, business_id: uuid.UUID
) -> CalendarConnection | None:
    result = await session.execute(
        business_scoped_select(CalendarConnection, business_id).execution_options(
            populate_existing=True
        )
    )
    return result.scalar_one_or_none()


async def upsert_tokens(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    encrypted_refresh_token: bytes,
    token_key_id: str,
    granted_scopes: str,
    synced_at: datetime,
) -> CalendarConnection:
    """Stores a freshly granted token, replacing any earlier one: the calendar choice is
    cleared and the status is `needs_selection`. One atomic statement, so a double-submitted
    callback cannot create two rows."""
    values = {
        "encrypted_refresh_token": encrypted_refresh_token,
        "token_key_id": token_key_id,
        "granted_scopes": granted_scopes,
        "calendar_id": None,
        "calendar_name": None,
        "connection_status": NEEDS_SELECTION,
        "last_error": None,
        "last_synced_at": synced_at,
    }
    stmt = pg_insert(CalendarConnection).values(business_id=business_id, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[CalendarConnection.business_id],
        set_={**values, "updated_at": func.clock_timestamp()},
    ).returning(CalendarConnection)
    result = await session.execute(stmt, execution_options={"populate_existing": True})
    return result.scalar_one()


async def set_selection(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    calendar_id: str,
    calendar_name: str,
    synced_at: datetime,
    seen_updated_at: datetime,
) -> CalendarConnection | None:
    return await _update(
        session,
        business_id,
        seen_updated_at,
        calendar_id=calendar_id,
        calendar_name=calendar_name,
        connection_status=CONNECTED,
        last_error=None,
        last_synced_at=synced_at,
    )


async def mark_status(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    status: str,
    last_error: str | None,
    last_synced_at: datetime | None,
    seen_updated_at: datetime,
    clear_selection: bool = False,
) -> CalendarConnection | None:
    values: dict[str, object] = {
        "connection_status": status,
        "last_error": last_error,
        "last_synced_at": last_synced_at,
    }
    if clear_selection:
        values |= {"calendar_id": None, "calendar_name": None}
    return await _update(session, business_id, seen_updated_at, **values)


async def reseal(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    encrypted_refresh_token: bytes,
    token_key_id: str,
    seen_updated_at: datetime,
) -> CalendarConnection | None:
    return await _update(
        session,
        business_id,
        seen_updated_at,
        encrypted_refresh_token=encrypted_refresh_token,
        token_key_id=token_key_id,
    )


async def delete_connection(
    session: AsyncSession, business_id: uuid.UUID
) -> CalendarConnection | None:
    result = await session.execute(
        delete(CalendarConnection)
        .where(CalendarConnection.business_id == business_id)
        .returning(CalendarConnection)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _update(
    session: AsyncSession,
    business_id: uuid.UUID,
    seen_updated_at: datetime,
    **values: object,
) -> CalendarConnection | None:
    """Updates only while the row still has `seen_updated_at`; otherwise returns the current
    row (None if it is gone) without changing anything."""
    result = await session.execute(
        update(CalendarConnection)
        .where(
            CalendarConnection.business_id == business_id,
            CalendarConnection.updated_at == seen_updated_at,
        )
        .values(**values, updated_at=func.clock_timestamp())
        .returning(CalendarConnection)
        .execution_options(populate_existing=True)
    )
    row = result.scalar_one_or_none()
    return row if row is not None else await get_connection(session, business_id)


class SqlCalendarStore:
    """Binds the functions above to one session. Satisfies `services.calendar.CalendarStore`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, business_id: uuid.UUID) -> CalendarConnection | None:
        return await get_connection(self._session, business_id)

    async def upsert_tokens(
        self,
        business_id: uuid.UUID,
        *,
        encrypted_refresh_token: bytes,
        token_key_id: str,
        granted_scopes: str,
        synced_at: datetime,
    ) -> CalendarConnection:
        return await upsert_tokens(
            self._session,
            business_id,
            encrypted_refresh_token=encrypted_refresh_token,
            token_key_id=token_key_id,
            granted_scopes=granted_scopes,
            synced_at=synced_at,
        )

    async def set_selection(
        self,
        business_id: uuid.UUID,
        *,
        calendar_id: str,
        calendar_name: str,
        synced_at: datetime,
        seen_updated_at: datetime,
    ) -> CalendarConnection | None:
        return await set_selection(
            self._session,
            business_id,
            calendar_id=calendar_id,
            calendar_name=calendar_name,
            synced_at=synced_at,
            seen_updated_at=seen_updated_at,
        )

    async def mark_status(
        self,
        business_id: uuid.UUID,
        *,
        status: str,
        last_error: str | None,
        last_synced_at: datetime | None,
        seen_updated_at: datetime,
        clear_selection: bool = False,
    ) -> CalendarConnection | None:
        return await mark_status(
            self._session,
            business_id,
            status=status,
            last_error=last_error,
            last_synced_at=last_synced_at,
            seen_updated_at=seen_updated_at,
            clear_selection=clear_selection,
        )

    async def reseal(
        self,
        business_id: uuid.UUID,
        *,
        encrypted_refresh_token: bytes,
        token_key_id: str,
        seen_updated_at: datetime,
    ) -> CalendarConnection | None:
        return await reseal(
            self._session,
            business_id,
            encrypted_refresh_token=encrypted_refresh_token,
            token_key_id=token_key_id,
            seen_updated_at=seen_updated_at,
        )

    async def delete(self, business_id: uuid.UUID) -> CalendarConnection | None:
        return await delete_connection(self._session, business_id)

    async def checkpoint(self) -> None:
        """Commits, so no transaction is open across a provider call or a failing request."""
        await self._session.commit()

    @asynccontextmanager
    async def savepoint(self) -> AsyncIterator[None]:
        """A nested unit of work: a DB error inside rolls back only what ran inside it."""
        async with self._session.begin_nested():
            yield
