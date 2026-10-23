"""Dependencies for booking rules: the settings defaults and the SQL store."""

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from models.booking_rules import BookingRulesRow
from repositories import booking_rules as repo
from services.booking_rules import BookingRules


def default_booking_rules() -> BookingRules:
    """The settings defaults; a dependency so tests can override it."""
    return BookingRules(
        slot_interval_minutes=settings.booking_slot_interval_minutes,
        min_notice_minutes=settings.booking_min_notice_minutes,
        max_days_ahead=settings.booking_max_days_ahead,
        buffer_minutes=settings.booking_buffer_minutes,
    )


def _to_rules(row: BookingRulesRow) -> BookingRules:
    return BookingRules(
        slot_interval_minutes=row.slot_interval_minutes,
        min_notice_minutes=row.min_notice_minutes,
        max_days_ahead=row.max_days_ahead,
        buffer_minutes=row.buffer_minutes,
    )


class SqlBookingRulesStore:
    """Implements `services.booking_rules.BookingRulesStore` over one session (in `core`
    because it speaks the service's dataclass, which repositories must not import)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_booking_rules(self, business_id: uuid.UUID) -> BookingRules | None:
        row = await repo.get_booking_rules(self._session, business_id)
        return None if row is None else _to_rules(row)

    async def upsert_booking_rules(
        self, business_id: uuid.UUID, rules: BookingRules
    ) -> BookingRules:
        return _to_rules(await repo.upsert_booking_rules(self._session, business_id, rules))


def get_booking_rules_store(session: AsyncSession = Depends(get_session)) -> SqlBookingRulesStore:
    return SqlBookingRulesStore(session)
