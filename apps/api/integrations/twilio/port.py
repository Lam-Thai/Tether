"""The Twilio SMS port: everything `services/twilio_sms.py` may ask of Twilio.

Raw provider: ONE attempt per call, no metering, no retries. The service wraps every
call in `retry_bounded` around `UsageGuard.metered(UsageKind.SMS)`. Twilio's Messages
API has no idempotency key, so only failures where Twilio certainly did NOT create the
message are `retryable` (connect errors, 429); a timeout or 5xx after the request was
sent is `send_unconfirmed` and never retried (a retry could text the owner twice).
"""

from dataclasses import dataclass
from typing import Protocol

# Fixed reason codes (never provider text).
REASON_TRANSPORT = "transport"  # could not connect: nothing was sent (retryable)
REASON_RATE_LIMITED = "rate_limited"  # 429: nothing was created (retryable)
REASON_SEND_UNCONFIRMED = "send_unconfirmed"  # timeout / 5xx / bad reply: may have been sent
REASON_INVALID_NUMBER = "invalid_number"  # Twilio 21211, 21614
REASON_UNVERIFIED_NUMBER = "unverified_number"  # Twilio 21608 (trial account, unverified "To")
REASON_AUTH_FAILED = "auth_failed"  # 401 / 20003: our credentials are wrong
REASON_PROVIDER_ERROR = "provider_error"


class TwilioApiError(Exception):
    """A Twilio call failed. The message is a fixed reason code, always safe to log.
    `error_code` is Twilio's numeric error code as a string, when the reply had one."""

    def __init__(
        self, reason: str, *, retryable: bool = False, error_code: str | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.error_code = error_code


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, TwilioApiError) and exc.retryable


@dataclass(frozen=True)
class SentSms:
    sid: str  # Twilio's MessageSid
    status: str  # accepted | queued | sending | sent | ...


class SmsApi(Protocol):
    async def send_sms(self, *, to: str, body: str, status_callback: str | None) -> SentSms:
        """Send one message from the configured number. Raises `TwilioApiError`."""
        ...


# Numbers (slice 14). Reads and webhook updates are idempotent (setting a URL twice is
# the same as once), so unlike a message create they are `retryable` on timeouts and
# 5xx too. There is deliberately NO purchase or release call: Tether never buys numbers.
REASON_NOT_FOUND = "not_found"  # 404 / 20404: the number isn't on this account


@dataclass(frozen=True)
class IncomingNumber:
    """One IncomingPhoneNumber on the account, with its current webhook configuration."""

    sid: str  # PN + 32 hex
    phone_number: str  # E.164
    friendly_name: str | None
    voice_url: str | None
    voice_method: str | None
    status_callback: str | None
    status_callback_method: str | None


@dataclass(frozen=True)
class AccountInfo:
    trial: bool  # Twilio account `type` is "Trial"


class NumbersApi(Protocol):
    async def account(self) -> AccountInfo:
        """The account's type. Raises `TwilioApiError`."""
        ...

    async def list_numbers(self) -> list[IncomingNumber]:
        """Every number on the account (first page of up to 100). Raises `TwilioApiError`."""
        ...

    async def get_number(self, sid: str) -> IncomingNumber:
        """One number; `TwilioApiError(REASON_NOT_FOUND)` when it isn't on the account."""
        ...

    async def set_webhooks(
        self, sid: str, *, voice_url: str, status_callback: str
    ) -> IncomingNumber:
        """Point the number's Voice URL and status callback (both POST) at these URLs; empty
        strings clear them. Returns the number as Twilio now reports it."""
        ...
