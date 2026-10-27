"""The real Twilio numbers adapter, over `httpx` (no Twilio SDK).

One attempt per call, timeout from settings. Credentials travel only in the Basic
`Authorization` header, never in a URL. Nothing here logs numbers or response bodies;
errors are fixed reason codes. These calls are idempotent, so timeouts and 5xx are
retryable (unlike a message create). `list_numbers` reads the first page only (100).
"""

import re
from typing import Any

import httpx

from integrations.twilio.client import API_BASE
from integrations.twilio.port import (
    REASON_AUTH_FAILED,
    REASON_NOT_FOUND,
    REASON_PROVIDER_ERROR,
    REASON_RATE_LIMITED,
    REASON_TRANSPORT,
    AccountInfo,
    IncomingNumber,
    TwilioApiError,
)

_AUTH_CODES = frozenset({"20003"})
_NOT_FOUND_CODES = frozenset({"20404"})
_NUMBER_SID = re.compile(r"^PN[0-9a-f]{32}$")
_PAGE_SIZE = 100


class HttpTwilioNumbersApi:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        api_base: str = API_BASE,
    ) -> None:
        self._account_url = f"{api_base}/Accounts/{account_sid}.json"
        self._numbers_url = f"{api_base}/Accounts/{account_sid}/IncomingPhoneNumbers"
        self._http = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            auth=httpx.BasicAuth(account_sid, auth_token),
        )

    async def account(self) -> AccountInfo:
        payload = await self._request("GET", self._account_url)
        return AccountInfo(trial=payload.get("type") == "Trial")

    async def list_numbers(self) -> list[IncomingNumber]:
        payload = await self._request(
            "GET", f"{self._numbers_url}.json", params={"PageSize": str(_PAGE_SIZE)}
        )
        items = payload.get("incoming_phone_numbers")
        if not isinstance(items, list):
            raise TwilioApiError(REASON_PROVIDER_ERROR)
        return [_number_from(item) for item in items]

    async def get_number(self, sid: str) -> IncomingNumber:
        payload = await self._request("GET", self._number_url(sid))
        return _number_from(payload)

    async def set_webhooks(
        self, sid: str, *, voice_url: str, status_callback: str
    ) -> IncomingNumber:
        form = {
            "VoiceUrl": voice_url,
            "VoiceMethod": "POST",
            "StatusCallback": status_callback,
            "StatusCallbackMethod": "POST",
        }
        payload = await self._request("POST", self._number_url(sid), data=form)
        return _number_from(payload)

    def _number_url(self, sid: str) -> str:
        if not isinstance(sid, str) or not _NUMBER_SID.fullmatch(sid):
            raise TwilioApiError(REASON_NOT_FOUND)
        return f"{self._numbers_url}/{sid}.json"

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise TwilioApiError(REASON_TRANSPORT, retryable=True) from None
        payload = _json_object(response)
        if 200 <= response.status_code < 300:
            return payload
        raise _error_for(response.status_code, payload)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _optional(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number_from(payload: Any) -> IncomingNumber:
    if not isinstance(payload, dict):
        raise TwilioApiError(REASON_PROVIDER_ERROR)
    sid, phone = payload.get("sid"), payload.get("phone_number")
    if not isinstance(sid, str) or not _NUMBER_SID.fullmatch(sid):
        raise TwilioApiError(REASON_PROVIDER_ERROR)
    if not isinstance(phone, str) or not phone:
        raise TwilioApiError(REASON_PROVIDER_ERROR)
    return IncomingNumber(
        sid=sid,
        phone_number=phone,
        friendly_name=_optional(payload.get("friendly_name")),
        voice_url=_optional(payload.get("voice_url")),
        voice_method=_optional(payload.get("voice_method")),
        status_callback=_optional(payload.get("status_callback")),
        status_callback_method=_optional(payload.get("status_callback_method")),
    )


def _error_for(status: int, payload: dict[str, Any]) -> TwilioApiError:
    raw = payload.get("code")
    code = str(raw) if isinstance(raw, int | str) and not isinstance(raw, bool) else None
    if code is not None and not code.isdigit():
        code = None
    if status == 429:
        return TwilioApiError(REASON_RATE_LIMITED, retryable=True, error_code=code)
    if status >= 500:
        return TwilioApiError(REASON_PROVIDER_ERROR, retryable=True, error_code=code)
    if status == 401 or code in _AUTH_CODES:
        return TwilioApiError(REASON_AUTH_FAILED, error_code=code)
    if status == 404 or code in _NOT_FOUND_CODES:
        return TwilioApiError(REASON_NOT_FOUND, error_code=code)
    return TwilioApiError(REASON_PROVIDER_ERROR, error_code=code)
