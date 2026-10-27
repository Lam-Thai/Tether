"""Dependencies for Twilio (slice 13; the voice slices reuse `TwilioWebhookAuth`).

Everything is overridable in tests. Without Twilio settings `get_sms_api` is `None`
and `get_notifier` falls back to `LogNotifier` (demo mode: nothing is texted).
"""

from functools import lru_cache
from urllib.parse import parse_qsl

from fastapi import Depends, Request

from core.config import settings
from core.errors import AppError
from core.usage import get_usage_guard
from integrations.twilio.client import HttpTwilioSmsApi
from integrations.twilio.port import SmsApi
from integrations.twilio.signature import is_valid_signature
from services.notifier import LogNotifier, Notifier
from services.twilio_sms import TwilioSmsNotifier
from services.usage import UsageGuard

SMS_STATUS_PATH = "/api/v1/webhooks/twilio/sms-status"


class InvalidSignatureError(AppError):
    status_code = 403
    code = "invalid_signature"

    def __init__(self) -> None:
        super().__init__("The request signature is invalid.")


MAX_WEBHOOK_BODY_BYTES = 64 * 1024


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"

    def __init__(self) -> None:
        super().__init__("The request body is too large.")


async def _read_capped_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_WEBHOOK_BODY_BYTES:
        raise PayloadTooLargeError
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            raise PayloadTooLargeError
    return bytes(body)


@lru_cache(maxsize=1)
def _build_api() -> SmsApi | None:
    if not settings.twilio_configured:
        return None
    assert settings.twilio_account_sid and settings.twilio_auth_token
    assert settings.twilio_from_number
    return HttpTwilioSmsApi(
        settings.twilio_account_sid,
        settings.twilio_auth_token.get_secret_value(),
        settings.twilio_from_number,
        settings.twilio_timeout_seconds,
    )


def get_sms_api() -> SmsApi | None:
    """One adapter per process (it holds an HTTP pool); `None` when Twilio isn't set up."""
    return _build_api()


def sms_status_callback_url() -> str | None:
    if not settings.api_public_base_url:
        return None
    return f"{settings.api_public_base_url}{SMS_STATUS_PATH}"


def get_notifier(
    usage: UsageGuard = Depends(get_usage_guard),
    api: SmsApi | None = Depends(get_sms_api),
) -> Notifier:
    if api is None:
        return LogNotifier()
    return TwilioSmsNotifier(api, usage, status_callback_url=sms_status_callback_url())


def public_request_url(request: Request) -> str:
    """The URL Twilio signed: the configured public origin + path (+ query), because
    behind a tunnel or proxy `request.url` shows the internal host."""
    if not settings.api_public_base_url:
        return str(request.url)
    url = f"{settings.api_public_base_url}{request.url.path}"
    return f"{url}?{request.url.query}" if request.url.query else url


class TwilioWebhookAuth:
    """FastAPI dependency for every Twilio-facing webhook (SMS status now, voice later):
    `fields: dict[str, str] = Depends(TwilioWebhookAuth())`. Verifies `X-Twilio-Signature`
    over ALL pairs of the urlencoded form body (repeated names included) and returns the
    form fields; bodies over 64 KiB are 413 `payload_too_large` before any hashing. 403
    `invalid_signature`
    when the signature is missing or wrong, or when Twilio isn't configured (no token to
    check against). The body is parsed here, so the route needs no multipart dependency."""

    async def __call__(self, request: Request) -> dict[str, str]:
        token = settings.twilio_auth_token
        if not settings.twilio_configured or token is None:
            raise InvalidSignatureError
        raw = (await _read_capped_body(request)).decode("utf-8", errors="replace")
        pairs = parse_qsl(raw, keep_blank_values=True)
        if not is_valid_signature(
            token.get_secret_value(),
            public_request_url(request),
            pairs,
            request.headers.get("X-Twilio-Signature"),
        ):
            raise InvalidSignatureError
        return dict(pairs)  # a repeated name keeps its last value
