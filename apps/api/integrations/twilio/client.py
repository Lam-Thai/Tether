"""The real Twilio SMS adapter, over `httpx` (no Twilio SDK).

One attempt per call, timeout from settings. The credentials travel only in the Basic
`Authorization` header, never in a URL (httpx logs URLs). Nothing here logs the message
body, the numbers or response bodies; errors are fixed reason codes.
"""

from typing import Any

import httpx

from integrations.twilio.port import (
    REASON_AUTH_FAILED,
    REASON_INVALID_NUMBER,
    REASON_PROVIDER_ERROR,
    REASON_RATE_LIMITED,
    REASON_SEND_UNCONFIRMED,
    REASON_TRANSPORT,
    REASON_UNVERIFIED_NUMBER,
    SentSms,
    TwilioApiError,
)

API_BASE = "https://api.twilio.com/2010-04-01"

_INVALID_NUMBER_CODES = frozenset({"21211", "21614"})
_UNVERIFIED_NUMBER_CODES = frozenset({"21608"})
_AUTH_CODES = frozenset({"20003"})


class HttpTwilioSmsApi:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        api_base: str = API_BASE,
    ) -> None:
        self._from = from_number
        self._url = f"{api_base}/Accounts/{account_sid}/Messages.json"
        self._http = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            auth=httpx.BasicAuth(account_sid, auth_token),
        )

    async def send_sms(self, *, to: str, body: str, status_callback: str | None) -> SentSms:
        form = {"To": to, "From": self._from, "Body": body}
        if status_callback:
            form["StatusCallback"] = status_callback
        try:
            response = await self._http.post(self._url, data=form)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise TwilioApiError(REASON_TRANSPORT, retryable=True) from None
        except httpx.HTTPError:
            # A timeout after sending, a connection dropped mid-request, ...: Twilio may
            # have created the message.
            raise TwilioApiError(REASON_SEND_UNCONFIRMED) from None
        payload = _json_object(response)
        if response.status_code in (200, 201):
            sid, status = payload.get("sid"), payload.get("status")
            if not isinstance(sid, str) or not sid or not isinstance(status, str):
                raise TwilioApiError(REASON_SEND_UNCONFIRMED)
            return SentSms(sid=sid, status=status)
        raise _error_for(response.status_code, payload)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_for(status: int, payload: dict[str, Any]) -> TwilioApiError:
    raw = payload.get("code")
    code = str(raw) if isinstance(raw, int | str) and not isinstance(raw, bool) else None
    if code is not None and not code.isdigit():
        code = None
    if status == 429:
        return TwilioApiError(REASON_RATE_LIMITED, retryable=True, error_code=code)
    if status >= 500:
        return TwilioApiError(REASON_SEND_UNCONFIRMED, error_code=code)
    if status == 401 or code in _AUTH_CODES:
        return TwilioApiError(REASON_AUTH_FAILED, error_code=code)
    if code in _INVALID_NUMBER_CODES:
        return TwilioApiError(REASON_INVALID_NUMBER, error_code=code)
    if code in _UNVERIFIED_NUMBER_CODES:
        return TwilioApiError(REASON_UNVERIFIED_NUMBER, error_code=code)
    return TwilioApiError(REASON_PROVIDER_ERROR, error_code=code)
