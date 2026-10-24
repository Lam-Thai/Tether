from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

BookingStatus = Literal["pending", "confirmed", "failed", "cancelled", "needs_reconciliation"]


class BookingRead(BaseModel):
    id: UUID
    service_name: str
    # In the business's timezone (ISO 8601 with its offset).
    start: datetime
    end: datetime
    # Local date and "HH:MM" start, what the agent told the customer.
    date: date
    label: str
    customer_name: str
    # Only `confirmed` is a real booking (an event exists on the owner's calendar).
    status: BookingStatus


class BookingListRead(BaseModel):
    bookings: list[BookingRead]
