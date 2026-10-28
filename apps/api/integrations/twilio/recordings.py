"""The real Twilio recordings adapter, over `httpx` (no Twilio SDK).

One attempt per call, timeout from settings. Credentials travel only in the Basic
`Authorization` header, never in a URL. Nothing here logs recording ids' bodies, audio or
response bodies; errors are fixed reason codes.

Policy:
- Every call is idempotent (a GET, a DELETE of something that may already be gone), so
  timeouts, 429 and 5xx are retryable. A fetch 404 is retryable too (a recording can lag
  the `<Record>` action briefly); a delete 404 is success (already deleted).
- The URL is always BUILT from our account sid and a validated recording sid. A URL from a
  webhook payload is never fetched: it would be an outbound request, with our credentials,
  to wherever the payload says.
- Twilio answers a media GET with a redirect to signed storage. We follow it, but httpx
  strips the `Authorization` header on a redirect to another origin, so our credentials
  never reach the storage host (a test pins this).
- The body is capped (`MAX_AUDIO_BYTES`) while streaming, and must be a RIFF/WAV file.
"""

import json
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
    RECORDING_SID,
    RecordingAudio,
    TwilioApiError,
)

MAX_AUDIO_BYTES = 4 * 1024 * 1024
_ERROR_BODY_BYTES = 64 * 1024
_AUTH_CODES = frozenset({"20003"})
_NOT_FOUND_CODES = frozenset({"20404"})
_CALL_SID = re.compile(r"^CA[0-9a-f]{32}$")
_PAGE_SIZE = 100
_DEFAULT_CONTENT_TYPE = "audio/x-wav"


class HttpTwilioRecordingsApi:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        api_base: str = API_BASE,
    ) -> None:
        self._recordings_url = f"{api_base}/Accounts/{account_sid}/Recordings"
        self._http = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            auth=httpx.BasicAuth(account_sid, auth_token),
            follow_redirects=True,
        )

    async def fetch(self, sid: str) -> RecordingAudio:
        _check_sid(sid)
        try:
            async with self._http.stream("GET", f"{self._recordings_url}/{sid}.wav") as response:
                if not 200 <= response.status_code < 300:
                    payload = _json_object(await _read_capped(response, _ERROR_BODY_BYTES))
                    raise _error_for(response.status_code, payload, not_found_retryable=True)
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
                    raise TwilioApiError(REASON_PROVIDER_ERROR)
                data = await _read_capped(response, MAX_AUDIO_BYTES)
                content_type = _content_type(response)
        except httpx.HTTPError:
            raise TwilioApiError(REASON_TRANSPORT, retryable=True) from None
        if not data.startswith(b"RIFF"):
            raise TwilioApiError(REASON_PROVIDER_ERROR)
        return RecordingAudio(data=data, content_type=content_type)

    async def delete(self, sid: str) -> None:
        _check_sid(sid)
        response = await self._send("DELETE", f"{self._recordings_url}/{sid}.json")
        if 200 <= response.status_code < 300 or response.status_code == 404:
            return
        raise _error_for(response.status_code, _json_object(response.content))

    async def list_for_call(self, call_sid: str) -> list[str]:
        if not isinstance(call_sid, str) or not _CALL_SID.fullmatch(call_sid):
            return []
        response = await self._send(
            "GET",
            f"{self._recordings_url}.json",
            params={"CallSid": call_sid, "PageSize": str(_PAGE_SIZE)},
        )
        payload = _json_object(response.content)
        if not 200 <= response.status_code < 300:
            raise _error_for(response.status_code, payload)
        items = payload.get("recordings")
        if not isinstance(items, list):
            raise TwilioApiError(REASON_PROVIDER_ERROR)
        sids: list[str] = []
        for item in items:
            sid = item.get("sid") if isinstance(item, dict) else None
            if isinstance(sid, str) and RECORDING_SID.fullmatch(sid):
                sids.append(sid)
        return sids

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise TwilioApiError(REASON_TRANSPORT, retryable=True) from None


def _check_sid(sid: str) -> None:
    if not isinstance(sid, str) or not RECORDING_SID.fullmatch(sid):
        raise TwilioApiError(REASON_NOT_FOUND)


async def _read_capped(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise TwilioApiError(REASON_PROVIDER_ERROR)
        chunks.append(chunk)
    return b"".join(chunks)


def _content_type(response: httpx.Response) -> str:
    raw = response.headers.get("content-type", "").split(";")[0].strip()
    return raw or _DEFAULT_CONTENT_TYPE


def _json_object(content: bytes) -> dict[str, Any]:
    try:
        body = json.loads(content)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_for(
    status: int, payload: dict[str, Any], *, not_found_retryable: bool = False
) -> TwilioApiError:
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
        return TwilioApiError(REASON_NOT_FOUND, retryable=not_found_retryable, error_code=code)
    return TwilioApiError(REASON_PROVIDER_ERROR, error_code=code)
