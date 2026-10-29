"""Real adapter for `UsersApi`: Clerk's Backend API over `httpx` (no Clerk SDK).

ONE attempt per call, with the timeout from settings. The secret key travels only in the
`Authorization` header, never in a URL (httpx logs URLs), and the user id is checked against
Clerk's id shape before it is placed in the URL. Errors are fixed reason codes; nothing here
logs or keeps a response body. Deleting a user is idempotent (a repeat is a 404, which counts
as success), so timeouts and 5xx are retryable.
"""

import re

import httpx

from integrations.clerk.port import (
    REASON_AUTH_FAILED,
    REASON_INVALID_USER_ID,
    REASON_PROVIDER_ERROR,
    REASON_RATE_LIMITED,
    REASON_TIMEOUT,
    REASON_TRANSPORT,
    ClerkApiError,
)

API_BASE = "https://api.clerk.com/v1"
_USER_ID = re.compile(r"^user_[A-Za-z0-9]{1,128}$")


class HttpClerkUsersApi:
    def __init__(
        self,
        secret_key: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        api_base: str = API_BASE,
    ) -> None:
        self._users_url = f"{api_base}/users"
        self._http = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {secret_key}"},
        )

    async def delete_user(self, user_id: str) -> None:
        if not isinstance(user_id, str) or not _USER_ID.fullmatch(user_id):
            raise ClerkApiError(REASON_INVALID_USER_ID)
        try:
            response = await self._http.delete(f"{self._users_url}/{user_id}")
        except httpx.TimeoutException:
            raise ClerkApiError(REASON_TIMEOUT, retryable=True) from None
        except httpx.HTTPError:
            raise ClerkApiError(REASON_TRANSPORT, retryable=True) from None
        status = response.status_code
        if 200 <= status < 300 or status == 404:
            return  # deleted, or already gone
        if status == 429:
            raise ClerkApiError(REASON_RATE_LIMITED, retryable=True)
        if status == 408:
            raise ClerkApiError(REASON_TIMEOUT, retryable=True)
        if status in (401, 403):
            raise ClerkApiError(REASON_AUTH_FAILED)
        raise ClerkApiError(REASON_PROVIDER_ERROR, retryable=status >= 500)
