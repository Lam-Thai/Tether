"""Self-serve account deletion (issue #50): "delete everything, including my sign-in".

`AccountEraser.erase` deletes the owner's whole footprint, in this order:

1. The typed phrase is checked first; a mismatch touches nothing (no provider call, no delete).
2. Stripe: the business's customer is deleted (its subscription ends at once, no proration,
   saved cards go). It runs FIRST and is the only step that aborts the deletion: any failure
   other than "already gone" is `BillingUnreachableError` (503), with nothing else touched, so
   an owner is never left with a live subscription and no account to cancel it from.
3. Google Calendar: the UPCOMING events Tether created are cancelled, best effort, through the
   same loop as "delete test data" (`services/event_cancellation.py`); past events stay on the
   owner's calendar. Then the stored token is revoked and deleted (best effort).
4. ONE database transaction deletes the owner's businesses (every tenant table cascades) and
   the `users` row, and is committed. Global usage rows (`business_id IS NULL`) and other
   businesses are never touched.
5. Clerk: the sign-in is deleted AFTER the commit, best effort (`sign_in_deleted: false` when it
   could not be). It is metered with no business, since the business row is gone.

No provider call is made inside an open transaction: the store commits before the Stripe call
and the calendar calls commit for themselves. Every step after Stripe is idempotent, so an
owner whose sign-in could not be removed just repeats the request: with no business left it
deletes the (re-created) user row and tries Clerk again. Without a business only the user and
the sign-in go. The user and the business always come from the tenant. No FastAPI or
SQLAlchemy imports.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from core.errors import AppError
from core.tenant import Tenant
from integrations.stripe.port import GatewayNotFoundError
from services.calendar import (
    CalendarError,
    CalendarNotConfiguredError,
    CalendarNotConnectedError,
)
from services.event_cancellation import DEADLINE_SECONDS, MAX_EVENT_CANCELS, cancel_events
from services.identity import IdentityError
from services.test_data import ConfirmationMismatchError

CONFIRMATION_PHRASE = "DELETE MY ACCOUNT"

logger = logging.getLogger("tether.account")


class BillingUnreachableError(AppError):
    status_code = 503
    code = "billing_unreachable"


@dataclass(frozen=True)
class AccountErased:
    businesses: int
    bookings: int
    # Google event ids of the DELETED bookings that may still be on the calendar: status
    # `confirmed` / `needs_reconciliation`, with an event id, starting from `upcoming_from`.
    calendar_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountDeletionReport:
    calendar_events_cancelled: int
    calendar_events_not_cancelled: int
    # None: there was no calendar connection to revoke. False: there was one, Google did not
    # confirm the revoke (the stored token is deleted anyway), so the owner must remove access.
    calendar_token_revoked: bool | None
    sign_in_deleted: bool


class AccountStore(Protocol):
    async def stripe_customer_id(self, business_id: uuid.UUID) -> str | None: ...

    async def upcoming_event_ids(self, business_id: uuid.UUID, *, from_time: datetime) -> list[str]:
        """Google event ids of this business's `confirmed` / `needs_reconciliation`
        bookings that start at or after `from_time`, oldest first."""
        ...

    async def delete_account(self, user_id: uuid.UUID, *, upcoming_from: datetime) -> AccountErased:
        """Deletes the user's businesses (everything under them cascades) and the user, in
        the caller's ONE transaction, and returns what was removed."""
        ...

    async def commit(self) -> None: ...


class IdentityDirectory(Protocol):
    async def delete_user(self, external_auth_id: str) -> None:
        """Removes the sign-in. Raises `IdentityError` (a fixed reason code)."""
        ...


class AccountEraser:
    def __init__(
        self,
        store: AccountStore,
        delete_customer: Callable[[uuid.UUID, str], Awaitable[None]],
        cancel_event: Callable[[uuid.UUID, str], Awaitable[None]],
        disconnect_calendar: Callable[[uuid.UUID], Awaitable[bool]],
        identity: IdentityDirectory,
        now_utc: Callable[[], datetime],
        *,
        deadline_seconds: float = DEADLINE_SECONDS,
        max_cancels: int = MAX_EVENT_CANCELS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._delete_customer = delete_customer
        self._cancel_event = cancel_event
        self._disconnect_calendar = disconnect_calendar
        self._identity = identity
        self._now_utc = now_utc
        self._deadline_seconds = deadline_seconds
        self._max_cancels = max_cancels
        self._monotonic = monotonic

    async def erase(self, tenant: Tenant, confirmation: str) -> AccountDeletionReport:
        if confirmation.strip() != CONFIRMATION_PHRASE:
            raise ConfirmationMismatchError(f"Type {CONFIRMATION_PHRASE} to confirm.")
        # Plain values first: the rows behind these objects are deleted below.
        user_id = tenant.user.id
        external_auth_id = tenant.user.external_auth_id
        business_id = tenant.business.id if tenant.business is not None else None
        now = self._now_utc()

        cancelled: set[str] = set()
        token_revoked: bool | None = None
        if business_id is not None:
            await self._end_billing(business_id)
            event_ids = await self._store.upcoming_event_ids(business_id, from_time=now)
            cancelled = await cancel_events(
                self._cancel_event,
                business_id,
                event_ids,
                deadline_seconds=self._deadline_seconds,
                max_cancels=self._max_cancels,
                monotonic=self._monotonic,
            )
            token_revoked = await self._revoke_token(business_id)

        erased = await self._store.delete_account(user_id, upcoming_from=now)
        await self._store.commit()  # before Clerk: the data is gone whatever happens next

        # Judged on the rows actually deleted, so an upcoming booking confirmed after the
        # snapshot (nobody tried to cancel it) is reported as not cancelled, never dropped.
        not_cancelled = len(set(erased.calendar_event_ids) - cancelled)
        sign_in_error = await self._delete_sign_in(external_auth_id)
        logger.info(
            "account deleted",
            extra={
                "user_id": str(user_id),
                "business_id": str(business_id) if business_id is not None else None,
                "businesses": erased.businesses,
                "bookings": erased.bookings,
                "events_cancelled": len(cancelled),
                "events_not_cancelled": not_cancelled,
                "token_revoked": token_revoked,
                "sign_in_error": sign_in_error,
            },
        )
        return AccountDeletionReport(
            calendar_events_cancelled=len(cancelled),
            calendar_events_not_cancelled=not_cancelled,
            calendar_token_revoked=token_revoked,
            sign_in_deleted=sign_in_error is None,
        )

    async def _end_billing(self, business_id: uuid.UUID) -> None:
        """Deletes the Stripe customer, if the business has one. Already gone is success; any
        other failure aborts the whole deletion before anything else is touched."""
        customer_id = await self._store.stripe_customer_id(business_id)
        if customer_id is None:
            return
        await self._store.commit()  # no transaction is held across the provider call
        try:
            await self._delete_customer(business_id, customer_id)
        except GatewayNotFoundError:
            return  # a retry after a partial deletion: the customer is already gone
        except Exception as exc:
            logger.warning(
                "stripe customer not deleted",
                extra={"business_id": str(business_id), "error_type": type(exc).__name__},
            )
            raise BillingUnreachableError(
                "We couldn't end your subscription with Stripe, so nothing was deleted. "
                "Try again in a few minutes."
            ) from None

    async def _revoke_token(self, business_id: uuid.UUID) -> bool | None:
        """True when Google confirmed the revoke, None when there was no connection (or no
        Google client configured, so none could exist), False when one existed and the revoke
        was not confirmed. Never raises a calendar error."""
        try:
            return await self._disconnect_calendar(business_id)
        except (CalendarNotConfiguredError, CalendarNotConnectedError):
            return None
        except CalendarError:
            return False

    async def _delete_sign_in(self, external_auth_id: str) -> str | None:
        """None when the sign-in is gone, else a fixed reason code. Never raises: the data is
        already deleted, so the owner must be told the truth rather than shown a 500."""
        try:
            await self._identity.delete_user(external_auth_id)
        except IdentityError as exc:
            return exc.reason
        except Exception:
            return "unexpected"
        return None
