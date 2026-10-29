"""The ports: how `core.auth` asks for the public key that signed a token, and how account
deletion asks Clerk to remove a user.

`SigningKeyResolver` is narrow on purpose — a callable from a raw JWT to its PEM-encoded
signing key — so `core.auth.verify_principal` never has to know whether that key came from
a live JWKS fetch or a test fixture.

`UsersApi` is the Backend API (it needs the secret key): ONE attempt per call, no metering,
no retries. `services/identity.py` wraps every call in `retry_bounded` around
`UsageGuard.metered(UsageKind.IDENTITY_CALL)`.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

SigningKeyResolver = Callable[[str], Awaitable[str]]

# Fixed reason codes (never provider text).
REASON_TRANSPORT = "transport"  # could not connect or the connection broke (retryable)
REASON_TIMEOUT = "timeout"  # no answer in time, or Clerk's 408 (retryable)
REASON_RATE_LIMITED = "rate_limited"  # 429 (retryable)
REASON_PROVIDER_ERROR = "provider_error"  # 5xx is retryable, any other 4xx is not
REASON_AUTH_FAILED = "auth_failed"  # 401 / 403: our secret key is wrong or lacks access
REASON_INVALID_USER_ID = "invalid_user_id"  # never sent: not a Clerk user id


class ClerkApiError(Exception):
    """A Clerk Backend API call failed. The message is a fixed reason code, always safe
    to log; `retryable` is true only for timeouts, transport errors, 408, 429 and 5xx."""

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, ClerkApiError) and exc.retryable


class UsersApi(Protocol):
    async def delete_user(self, user_id: str) -> None:
        """Deletes the Clerk user (their sign-in). A user that is already gone counts as
        deleted. Raises `ClerkApiError`."""
        ...
