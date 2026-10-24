"""Builds `BookingService` over one session, for the conversation engine's `create_booking`
tool (`core/conversation.py`) and the bookings routes. One builder so both get the same
directory, availability recheck and calendar connection rules."""

from collections.abc import Callable
from datetime import datetime

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.booking_rules import default_booking_rules
from core.calendar import get_calendar_connections, get_clock
from core.db import get_session
from core.directory import SqlDirectoryStore
from repositories.bookings import SqlBookingStore
from services.availability import AvailabilityService
from services.booking import BookingService
from services.booking_rules import BookingRules
from services.calendar import CalendarConnections
from services.directory import BusinessDirectory


def build_booking_service(
    directory: BusinessDirectory, connections: CalendarConnections, store: SqlBookingStore
) -> BookingService:
    availability = AvailabilityService(directory, connections, savepoint=store.savepoint)
    return BookingService(directory, availability, connections, store)


def get_booking_service(
    session: AsyncSession = Depends(get_session),
    connections: CalendarConnections = Depends(get_calendar_connections),
    booking_defaults: BookingRules = Depends(default_booking_rules),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> BookingService:
    directory = BusinessDirectory(SqlDirectoryStore(session), clock, booking_defaults)
    return build_booking_service(directory, connections, SqlBookingStore(session))
