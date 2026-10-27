"""`PhoneNumberService`: the owner associates a Twilio number with their business.

Small surface (`overview`, `associate`, `test`, `disconnect`) over:

- One platform Twilio account. The owner sees only the account's UNASSIGNED numbers plus
  their own; a number another business holds is never shown (not even as "in use").
- A number belongs to exactly one business: the UNIQUE `phone_number` and
  `provider_number_sid` decide a race; the loser gets 409 `phone_number_in_use`.
- Associating points the number's Voice URL and status callback at this API (the same
  URLs for every business: the inbound number decides the business), then verifies
  Twilio reports exactly that. `test` re-verifies; someone editing the number in the
  Twilio console shows up as `misconfigured` with a fixed `last_error` code.
- It NEVER buys or releases a number: the port has no such call.
- Every Twilio call is `retry_bounded(TELEPHONY_RETRY)` around
  `usage.metered(TELEPHONY_API)` (metered only, never capped), and the store checkpoints
  before it, so no transaction is held across a provider call.

No FastAPI or SQLAlchemy imports: the caller passes a `PhoneNumberStore` and the port.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar
from urllib.parse import urlsplit

from core.errors import AppError
from core.retry import RetryPolicy, retry_bounded
from core.tenant import Tenant
from integrations.twilio.port import (
    REASON_NOT_FOUND,
    IncomingNumber,
    NumbersApi,
    TwilioApiError,
    is_retryable,
)
from models.phone_number import PhoneNumber
from services.agent import require_business
from services.usage import LimitExceededError, UsageGuard, UsageKind, UsageUnavailableError

logger = logging.getLogger("tether.phone_numbers")

TELEPHONY_RETRY = RetryPolicy(2, 0.5)
# Where a new Twilio number's Voice URL points by default (the trial greeting).
TWILIO_DEMO_HOST = "demo.twilio.com"

T = TypeVar("T")


class PhoneNumberNotFoundError(AppError):
    status_code = 404
    code = "phone_number_not_found"


class PhoneNumberInUseError(AppError):
    status_code = 409
    code = "phone_number_in_use"


class TelephonyNotConfiguredError(AppError):
    status_code = 503
    code = "telephony_not_configured"


class TelephonyUnavailableError(AppError):
    status_code = 503
    code = "telephony_unavailable"


class PhoneNumberStore(Protocol):
    async def list_for_business(self, business_id: uuid.UUID) -> Sequence[PhoneNumber]: ...
    async def get(self, business_id: uuid.UUID, number_id: uuid.UUID) -> PhoneNumber | None: ...
    async def get_by_sid(
        self, business_id: uuid.UUID, provider_number_sid: str
    ) -> PhoneNumber | None: ...
    async def associated_sids(self, sids: Sequence[str]) -> set[str]:
        """Which of these sids ANY business holds."""
        ...

    async def insert(
        self,
        *,
        business_id: uuid.UUID,
        phone_number: str,
        provider_number_sid: str,
        status: str,
        last_error: str | None,
        verified_at: datetime | None,
    ) -> PhoneNumber | None:
        """None when the number is already held (by anyone)."""
        ...

    async def save(self, number: PhoneNumber) -> PhoneNumber: ...
    async def delete(self, business_id: uuid.UUID, number_id: uuid.UUID) -> bool: ...
    async def checkpoint(self) -> None:
        """Commit and end the transaction (called before every Twilio call)."""
        ...


@dataclass(frozen=True)
class WebhookTargets:
    """Where every associated number must point. Both None: telephony isn't configured
    (no Twilio credentials or no public API origin), so nothing can be associated."""

    voice_url: str | None
    status_callback: str | None

    @property
    def configured(self) -> bool:
        return bool(self.voice_url and self.status_callback)


@dataclass(frozen=True)
class AvailableNumber:
    provider_number_sid: str
    phone_number: str
    friendly_name: str | None


@dataclass(frozen=True)
class PhoneOverview:
    configured: bool
    trial: bool | None  # None: unknown (not configured, or Twilio unreachable)
    numbers: Sequence[PhoneNumber]
    available: Sequence[AvailableNumber]
    available_error: str | None  # "not_configured" | "unavailable"


class PhoneNumberService:
    def __init__(
        self,
        store: PhoneNumberStore,
        api: NumbersApi | None,
        usage: UsageGuard,
        targets: WebhookTargets,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        retry: RetryPolicy = TELEPHONY_RETRY,
    ) -> None:
        self._store = store
        self._api = api
        self._usage = usage
        self._targets = targets
        self._clock = clock
        self._retry = retry

    async def overview(self, tenant: Tenant) -> PhoneOverview:
        business_id = require_business(tenant).id
        numbers = list(await self._store.list_for_business(business_id))
        if self._api is None or not self._targets.configured:
            return PhoneOverview(False, None, numbers, [], "not_configured")
        api = self._api
        await self._store.checkpoint()
        try:
            trial: bool | None = (await self._call(business_id, api.account)).trial
        except TelephonyUnavailableError:
            trial = None
        try:
            on_account = await self._call(business_id, api.list_numbers)
        except TelephonyUnavailableError:
            return PhoneOverview(True, trial, numbers, [], "unavailable")
        held = await self._store.associated_sids([n.sid for n in on_account])
        available = [
            AvailableNumber(n.sid, n.phone_number, n.friendly_name)
            for n in on_account
            if n.sid not in held and self._free(n)
        ]
        return PhoneOverview(True, trial, numbers, available, None)

    async def associate(self, tenant: Tenant, provider_number_sid: str) -> PhoneNumber:
        business_id = require_business(tenant).id
        api = self._require_configured()
        if await self._store.associated_sids([provider_number_sid]):
            # Re-associating our own number re-applies the webhooks (idempotent); anyone
            # else's is in use, decided before any Twilio call.
            if await self._store.get_by_sid(business_id, provider_number_sid) is None:
                raise PhoneNumberInUseError("That number is already connected to a business.")
        await self._store.checkpoint()
        try:
            current = await self._call(business_id, lambda: api.get_number(provider_number_sid))
            if not self._free(current):
                # Configured for something else on the account: never overwrite it.
                raise PhoneNumberInUseError("That number is already in use.")
            number = await self._call(
                business_id,
                lambda: api.set_webhooks(
                    provider_number_sid,
                    voice_url=self._targets.voice_url or "",
                    status_callback=self._targets.status_callback or "",
                ),
            )
        except _NumberMissingError:
            raise PhoneNumberNotFoundError("That number isn't on the Twilio account.") from None
        status, error = self._verify(number)
        verified_at = self._clock() if status == "connected" else None

        existing = await self._store.get_by_sid(business_id, provider_number_sid)
        if existing is not None:
            existing.status, existing.last_error, existing.verified_at = status, error, verified_at
            return await self._store.save(existing)
        row = await self._store.insert(
            business_id=business_id,
            phone_number=number.phone_number,
            provider_number_sid=number.sid,
            status=status,
            last_error=error,
            verified_at=verified_at,
        )
        if row is None:
            # Another business won the race. The webhooks we set are the same for every
            # business (the number decides the tenant), so nothing needs undoing.
            raise PhoneNumberInUseError("That number is already connected to a business.")
        logger.info(
            "phone number associated",
            extra={"business_id": str(business_id), "number_id": str(row.id), "status": status},
        )
        return row

    async def test(self, tenant: Tenant, number_id: uuid.UUID) -> PhoneNumber:
        business_id = require_business(tenant).id
        row = await self._store.get(business_id, number_id)
        if row is None:
            raise PhoneNumberNotFoundError("Phone number not found.")
        api = self._require_configured()
        sid = row.provider_number_sid
        await self._store.checkpoint()
        try:
            number = await self._call(business_id, lambda: api.get_number(sid))
        except _NumberMissingError:
            row.status, row.last_error = "misconfigured", "number_missing"
            return await self._store.save(row)
        except TelephonyUnavailableError:
            # The check couldn't run: keep the last known status, say why it's stale.
            row.last_error = "unavailable"
            return await self._store.save(row)
        row.status, row.last_error = self._verify(number)
        if row.status == "connected":
            row.verified_at = self._clock()
        return await self._store.save(row)

    async def disconnect(self, tenant: Tenant, number_id: uuid.UUID) -> bool:
        """Deletes the association. Returns whether the number no longer points at this
        API on Twilio (False: Twilio couldn't confirm it; the owner can clear it in the
        console). Calls to a disconnected number get the polite unknown-number reply."""
        business_id = require_business(tenant).id
        row = await self._store.get(business_id, number_id)
        if row is None:
            raise PhoneNumberNotFoundError("Phone number not found.")
        sid = row.provider_number_sid
        cleared = True
        if self._api is not None:
            api = self._api
            await self._store.checkpoint()
            try:
                number = await self._call(business_id, lambda: api.get_number(sid))
                # Only undo our own configuration, field by field; never wipe what
                # someone set since (a console edit of just the Voice URL is kept).
                voice = "" if number.voice_url == self._targets.voice_url else number.voice_url
                status = (
                    ""
                    if number.status_callback == self._targets.status_callback
                    else number.status_callback
                )
                if (voice, status) != (number.voice_url, number.status_callback):
                    await self._call(
                        business_id,
                        lambda: api.set_webhooks(
                            sid, voice_url=voice or "", status_callback=status or ""
                        ),
                    )
            except _NumberMissingError:
                pass  # gone from the account: nothing points here any more
            except TelephonyUnavailableError:
                cleared = False
        await self._store.delete(business_id, number_id)
        logger.info(
            "phone number disconnected",
            extra={"business_id": str(business_id), "number_id": str(number_id)},
        )
        return cleared

    def _require_configured(self) -> NumbersApi:
        if self._api is None or not self._targets.configured:
            raise TelephonyNotConfiguredError("Phone numbers aren't set up for this deployment.")
        return self._api

    def _free(self, number: IncomingNumber) -> bool:
        """A number Tether may take: no Voice URL, Twilio's own demo greeting (a fresh
        trial number), or already ours. Anything else is in use for another purpose."""
        url = number.voice_url
        return (
            not url or url == self._targets.voice_url or urlsplit(url).hostname == TWILIO_DEMO_HOST
        )

    def _verify(self, number: IncomingNumber) -> tuple[str, str | None]:
        if number.voice_url != self._targets.voice_url or (number.voice_method or "POST") != "POST":
            return "misconfigured", "voice_url_mismatch"
        if (
            number.status_callback != self._targets.status_callback
            or (number.status_callback_method or "POST") != "POST"
        ):
            return "misconfigured", "status_callback_mismatch"
        return "connected", None

    async def _call(self, business_id: uuid.UUID, call: Callable[[], Awaitable[T]]) -> T:
        async def attempt() -> T:
            async with self._usage.metered(UsageKind.TELEPHONY_API, business_id=business_id):
                return await call()

        try:
            return await retry_bounded(attempt, self._retry, is_retryable)
        except TwilioApiError as exc:
            if exc.reason == REASON_NOT_FOUND:
                raise _NumberMissingError from None
            logger.warning("twilio numbers call failed", extra={"reason": exc.reason})
            raise TelephonyUnavailableError("Twilio didn't respond. Try again shortly.") from None
        except (LimitExceededError, UsageUnavailableError):
            # Metered-only kind: not expected, but never let it escape as a 429/503 code
            # that names a different feature.
            raise TelephonyUnavailableError("Twilio didn't respond. Try again shortly.") from None


class _NumberMissingError(Exception):
    """Twilio says the number isn't on the account (internal; mapped by each caller)."""
