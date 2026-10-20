"""Repository for `subscriptions` and `stripe_events`.

Like `businesses`, a subscription row is reached by `business_id` resolved
from `TenantContext`, or by a Stripe customer id we created ourselves (the
webhook path, which has no tenant). The `lock_*` functions take a row lock
for the rest of the transaction so checkout creation and webhook
processing for one business run one at a time.
"""

import uuid
from collections.abc import Awaitable

from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AppError
from models.billing import StripeEvent, Subscription

# Shorter than the connection's 5s statement_timeout so a waiter gets a clean
# 409 instead of a cancelled statement while another request holds the row.
LOCK_TIMEOUT = "3s"
_LOCK_NOT_AVAILABLE = "55P03"
_QUERY_CANCELED = "57014"


class BillingBusyError(AppError):
    status_code = 409
    code = "billing_busy"


async def _locked[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) in (_LOCK_NOT_AVAILABLE, _QUERY_CANCELED):
            raise BillingBusyError(
                "Another billing request for this business is in progress. Try again shortly."
            ) from exc
        raise


async def _set_lock_timeout(session: AsyncSession) -> None:
    await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))


async def _select_for_update(session: AsyncSession, query: Select) -> Subscription | None:
    await _set_lock_timeout(session)
    result = await _locked(
        session.execute(
            query.with_for_update()
            # Re-read under the lock even if this session already holds the object.
            .execution_options(populate_existing=True)
        )
    )
    return result.scalar_one_or_none()


async def get_subscription_for_business(
    session: AsyncSession, business_id: uuid.UUID
) -> Subscription | None:
    result = await session.execute(
        select(Subscription).where(Subscription.business_id == business_id)
    )
    return result.scalar_one_or_none()


async def lock_subscription_for_business(
    session: AsyncSession, business_id: uuid.UUID
) -> Subscription:
    """Creates the row if missing, then locks it (`SELECT ... FOR UPDATE`)."""
    await _set_lock_timeout(session)
    # Can wait on a concurrent first insert for the same business.
    await _locked(
        session.execute(
            pg_insert(Subscription)
            .values(business_id=business_id)
            .on_conflict_do_nothing(index_elements=[Subscription.business_id])
        )
    )
    row = await _select_for_update(
        session, select(Subscription).where(Subscription.business_id == business_id)
    )
    assert row is not None
    return row


async def lock_subscription_for_customer(
    session: AsyncSession, customer_id: str
) -> Subscription | None:
    return await _select_for_update(
        session, select(Subscription).where(Subscription.stripe_customer_id == customer_id)
    )


async def record_stripe_event(session: AsyncSession, event_id: str, event_type: str) -> bool:
    """True the first time an event id is seen, False for a redelivery."""
    result = await session.execute(
        pg_insert(StripeEvent)
        .values(event_id=event_id, type=event_type)
        .on_conflict_do_nothing(index_elements=[StripeEvent.event_id])
        .returning(StripeEvent.event_id)
    )
    return result.scalar_one_or_none() is not None


class SqlBillingStore:
    """Binds the functions above to one session, matching `services.billing.BillingStore`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_business(self, business_id: uuid.UUID) -> Subscription | None:
        return await get_subscription_for_business(self._session, business_id)

    async def lock_for_business(self, business_id: uuid.UUID) -> Subscription:
        return await lock_subscription_for_business(self._session, business_id)

    async def lock_for_customer(self, customer_id: str) -> Subscription | None:
        return await lock_subscription_for_customer(self._session, customer_id)

    async def record_event(self, event_id: str, event_type: str) -> bool:
        return await record_stripe_event(self._session, event_id, event_type)

    async def flush(self) -> None:
        await self._session.flush()
