"""Repository for `booking_rules`. Every query is scoped by `business_id`.

Returns ORM rows and accepts anything with the four rule attributes (so it never
imports `services`); `core/booking_rules.SqlBookingRulesStore` adapts it to the
service's `BookingRules` dataclass.
"""

import uuid
from typing import Protocol

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.booking_rules import BookingRulesRow
from repositories.base import business_scoped_select


class RuleValues(Protocol):
    @property
    def slot_interval_minutes(self) -> int: ...
    @property
    def min_notice_minutes(self) -> int: ...
    @property
    def max_days_ahead(self) -> int: ...
    @property
    def buffer_minutes(self) -> int: ...


async def get_booking_rules(
    session: AsyncSession, business_id: uuid.UUID
) -> BookingRulesRow | None:
    result = await session.execute(business_scoped_select(BookingRulesRow, business_id))
    return result.scalar_one_or_none()


async def upsert_booking_rules(
    session: AsyncSession, business_id: uuid.UUID, rules: RuleValues
) -> BookingRulesRow:
    """INSERT ... ON CONFLICT (business_id) DO UPDATE, in the caller's transaction."""
    values = {
        "slot_interval_minutes": rules.slot_interval_minutes,
        "min_notice_minutes": rules.min_notice_minutes,
        "max_days_ahead": rules.max_days_ahead,
        "buffer_minutes": rules.buffer_minutes,
    }
    stmt = (
        insert(BookingRulesRow)
        .values(id=uuid.uuid4(), business_id=business_id, **values)
        .on_conflict_do_update(
            index_elements=[BookingRulesRow.business_id],
            set_={**values, "updated_at": func.clock_timestamp()},
        )
        .returning(BookingRulesRow)
    )
    result = await session.execute(stmt, execution_options={"populate_existing": True})
    return result.scalar_one()
