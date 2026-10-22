"""Business workspace service — the deep module behind `/api/v1/business`.

No FastAPI or SQLAlchemy imports here (see root `CLAUDE.md`): the router
binds the DB session onto each repository function with `functools.partial`
(same pattern as `services/health.py`) and passes the bound callables in,
so this module only ever sees `Tenant` and plain callables — never a raw
`business_id` from the client and never a `Session`.
"""

import uuid
from collections.abc import Awaitable, Callable

from core.errors import AppError
from core.tenant import Tenant
from models.business import Business

GetBusinessByIdForOwner = Callable[[uuid.UUID, uuid.UUID], Awaitable[Business | None]]
CreateBusiness = Callable[..., Awaitable[Business]]
UpdateBusiness = Callable[..., Awaitable[Business]]


class BusinessNotFoundError(AppError):
    status_code = 404
    code = "business_not_found"


class BusinessAlreadyExistsError(AppError):
    status_code = 409
    code = "business_already_exists"


async def get_current_business(tenant: Tenant) -> Business:
    if tenant.business is None:
        raise BusinessNotFoundError("You haven't created a business yet.")
    return tenant.business


async def create_business(
    create: CreateBusiness,
    tenant: Tenant,
    *,
    name: str,
    industry: str,
    address: str,
    timezone: str,
    phone: str | None,
    contact_email: str | None,
    currency: str = "USD",
) -> Business:
    if tenant.business is not None:
        raise BusinessAlreadyExistsError("You already have a business.")
    return await create(
        owner_id=tenant.user.id,
        name=name,
        industry=industry,
        address=address,
        timezone=timezone,
        phone=phone,
        contact_email=contact_email,
        currency=currency,
    )


async def update_business(
    get_by_id_for_owner: GetBusinessByIdForOwner,
    update: UpdateBusiness,
    tenant: Tenant,
    business_id: uuid.UUID,
    **fields: str | None,
) -> Business:
    business = await get_by_id_for_owner(business_id, tenant.user.id)
    if business is None:
        # Same error for "doesn't exist" and "belongs to someone else" —
        # never leak which one it was.
        raise BusinessNotFoundError("Business not found.")
    return await update(business, **fields)
