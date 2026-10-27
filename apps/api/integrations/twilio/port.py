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
