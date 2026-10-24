"""`/api/v1/bookings`: the bookings the agent made. Router only.

For now one read: the booking attempts of one conversation (the test chat's side
panel). Entitled tenant; the business always comes from the tenant, so another
business's interaction id simply lists nothing.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from core.billing import get_entitled_tenant
from core.booking import get_booking_service
from core.tenant import Tenant
from schemas.booking import BookingListRead, BookingRead
from services.booking import BookingService, BookingSummary

router = APIRouter(tags=["bookings"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Bookings = Annotated[BookingService, Depends(get_booking_service)]


def _read(booking: BookingSummary) -> BookingRead:
    return BookingRead(
        id=booking.id,
        service_name=booking.service_name,
        start=booking.start,
        end=booking.end,
        date=booking.start.date(),
        label=booking.start.strftime("%H:%M"),
        customer_name=booking.customer_name,
        status=booking.status,  # type: ignore[arg-type]
    )


@router.get("/bookings", response_model=BookingListRead)
async def list_bookings(
    tenant: Entitled, bookings: Bookings, interaction_id: Annotated[UUID, Query()]
) -> BookingListRead:
    listed = await bookings.for_interaction(tenant, interaction_id)
    return BookingListRead(bookings=[_read(b) for b in listed])
