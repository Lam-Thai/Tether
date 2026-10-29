"""Removing a person's sign-in (Clerk) when they delete their account.

`MeteredIdentityDirectory.delete_user` is `retry_bounded(IDENTITY_RETRY)` around
`usage.metered(UsageKind.IDENTITY_CALL)` around ONE adapter attempt. The call is metered
globally (`business_id=None`): it runs after the business row is gone, so a
business-attributed usage row would break its foreign key. The kind is never capped. Every
failure is an `IdentityError` carrying a fixed reason code, never provider text, so the
account-deletion report can say the sign-in is still there without leaking anything.
Unconfigured (no Clerk secret key) is the same error with no call and no metering. No
FastAPI or SQLAlchemy imports.
"""

from core.retry import RetryPolicy, retry_bounded
from integrations.clerk.port import ClerkApiError, UsersApi, is_retryable
from services.usage import LimitExceededError, UsageGuard, UsageKind, UsageUnavailableError

# Deleting a user is idempotent (a repeat is "already gone", which the adapter reports as
# success), so one retry on a timeout or 5xx is safe.
IDENTITY_RETRY = RetryPolicy(attempts=2, backoff_seconds=0.2)

REASON_NOT_CONFIGURED = "not_configured"
REASON_USAGE_LIMITED = "usage_limited"
REASON_USAGE_UNAVAILABLE = "usage_unavailable"


class IdentityError(Exception):
    """The sign-in could not be removed. The message is a fixed reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MeteredIdentityDirectory:
    def __init__(
        self, api: UsersApi, usage: UsageGuard, retry: RetryPolicy = IDENTITY_RETRY
    ) -> None:
        self._api = api
        self._usage = usage
        self._retry = retry

    async def delete_user(self, external_auth_id: str) -> None:
        async def attempt() -> None:
            async with self._usage.metered(UsageKind.IDENTITY_CALL, business_id=None):
                await self._api.delete_user(external_auth_id)

        try:
            await retry_bounded(attempt, self._retry, is_retryable)
        except ClerkApiError as exc:
            raise IdentityError(exc.reason) from None
        except LimitExceededError:
            raise IdentityError(REASON_USAGE_LIMITED) from None
        except UsageUnavailableError:
            raise IdentityError(REASON_USAGE_UNAVAILABLE) from None


class UnconfiguredIdentityDirectory:
    """Demo mode: no Clerk secret key, so nothing can be deleted at Clerk."""

    async def delete_user(self, external_auth_id: str) -> None:
        raise IdentityError(REASON_NOT_CONFIGURED)
