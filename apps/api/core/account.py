"""Dependencies for self-serve account deletion (issue #50).

The SQL store lives here, not in `repositories/`, because it speaks the service's dataclass;
the SQL itself is in `repositories/account.py`. The Clerk Backend API is built only when
`CLERK_SECRET_KEY` is set; without it deleting an account removes all Tether data and reports
that the sign-in could not be removed (`UnconfiguredIdentityDirectory`).
"""

import uuid
from collections.abc import Callable
from datetime import datetime

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_billing_gateway
from core.calendar import get_calendar_connections, get_clock
from core.config import settings
from core.db import get_session
from core.usage import get_usage_guard
from integrations.clerk.port import UsersApi
from integrations.clerk.users import HttpClerkUsersApi
from integrations.stripe.port import BillingGateway
from repositories import account as repo
from services.account import AccountErased, AccountEraser, IdentityDirectory
from services.calendar import CalendarConnections
from services.identity import MeteredIdentityDirectory, UnconfiguredIdentityDirectory
from services.usage import UsageGuard


class SqlAccountStore:
    """`services.account.AccountStore` over the request's session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def stripe_customer_id(self, business_id: uuid.UUID) -> str | None:
        return await repo.stripe_customer_id(self._session, business_id)

    async def upcoming_event_ids(self, business_id: uuid.UUID, *, from_time: datetime) -> list[str]:
        return await repo.upcoming_event_ids(self._session, business_id, from_time)

    async def delete_account(self, user_id: uuid.UUID, *, upcoming_from: datetime) -> AccountErased:
        businesses, bookings, event_ids = await repo.delete_account(
            self._session, user_id, upcoming_from
        )
        return AccountErased(businesses, bookings, tuple(event_ids))

    async def commit(self) -> None:
        await self._session.commit()


# One client per process (the secret is only ever in its Authorization header); tests
# override `get_clerk_users_api`.
_users_api: UsersApi | None = (
    HttpClerkUsersApi(settings.clerk_secret_key.get_secret_value(), settings.clerk_timeout_seconds)
    if settings.clerk_secret_key is not None
    else None
)


def get_clerk_users_api() -> UsersApi | None:
    """None when `CLERK_SECRET_KEY` is not set."""
    return _users_api


def get_identity_directory(
    api: UsersApi | None = Depends(get_clerk_users_api),
    usage: UsageGuard = Depends(get_usage_guard),
) -> IdentityDirectory:
    if api is None:
        return UnconfiguredIdentityDirectory()
    return MeteredIdentityDirectory(api, usage)


def get_account_eraser(
    session: AsyncSession = Depends(get_session),
    gateway: BillingGateway = Depends(get_billing_gateway),
    connections: CalendarConnections = Depends(get_calendar_connections),
    identity: IdentityDirectory = Depends(get_identity_directory),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> AccountEraser:
    async def delete_customer(business_id: uuid.UUID, customer_id: str) -> None:
        await gateway.delete_customer(customer_id=customer_id, business_id=business_id)

    async def disconnect_calendar(business_id: uuid.UUID) -> bool:
        return (await connections.disconnect(business_id)).revoked

    return AccountEraser(
        SqlAccountStore(session),
        delete_customer,
        connections.cancel_event,
        disconnect_calendar,
        identity,
        clock,
    )
