"""Repository for `businesses` — the tenant root.

Every owner-facing lookup here is paired with the caller's `owner_id` (resolved
from `TenantContext`, never from a client-supplied `business_id`). The one exception
is `get_business_for_stored_row`, for tenant-less provider webhooks whose business id
comes from a stored row the webhook resolved (the phone number a call came in on),
never from a request. Later
domain repositories (FAQs, services, ...) scope by `tenant.business_id`
instead — see `repositories/base.py`.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.business import Business


async def get_business_by_owner_id(session: AsyncSession, owner_id: uuid.UUID) -> Business | None:
    result = await session.execute(select(Business).where(Business.owner_id == owner_id))
    return result.scalar_one_or_none()


async def get_business_for_stored_row(
    session: AsyncSession, business_id: uuid.UUID
) -> Business | None:
    """Only with an id read from a stored row (see the module docstring)."""
    return await session.get(Business, business_id)


async def get_business_by_id_for_owner(
    session: AsyncSession, business_id: uuid.UUID, owner_id: uuid.UUID
) -> Business | None:
    """Returns `None` both when the id doesn't exist and when it belongs to
    a different owner — callers (services/business.py) must treat both the
    same way (404), never leaking which case it was.
    """
    result = await session.execute(
        select(Business).where(Business.id == business_id, Business.owner_id == owner_id)
    )
    return result.scalar_one_or_none()


async def create_business(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    name: str,
    industry: str,
    address: str,
    timezone: str,
    phone: str | None,
    contact_email: str | None,
    currency: str = "USD",
) -> Business:
    business = Business(
        owner_id=owner_id,
        name=name,
        industry=industry,
        address=address,
        timezone=timezone,
        phone=phone,
        contact_email=contact_email,
        currency=currency,
    )
    session.add(business)
    await session.flush()
    return business


async def update_business(
    session: AsyncSession,
    business: Business,
    **fields: str | None,
) -> Business:
    """Applies only the fields explicitly passed (partial update)."""
    for field, value in fields.items():
        setattr(business, field, value)
    await session.flush()
    # `updated_at` is server-computed (`onupdate=func.now()`); refresh so
    # the caller never hands back a stale value to `BusinessRead`.
    await session.refresh(business)
    return business
