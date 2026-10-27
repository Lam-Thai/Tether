"""Twilio settings validation and the dependencies in `core/twilio.py`."""

from urllib.parse import urlencode

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from core import twilio as twilio_deps
from core.config import Settings, settings
from core.errors import register_exception_handlers
from integrations.twilio.fake import FakeSmsApi
from integrations.twilio.signature import compute_signature
from services.notifier import LogNotifier
from services.twilio_sms import TwilioSmsNotifier
from tests.services.memory_usage_store import guard

SID = "AC" + "b" * 32
TOKEN = "unit-test-token"
PUBLIC = "https://api.example.test"
STRIPE = {
    "stripe_secret_key": "sk_test_placeholder",
    "stripe_webhook_secret": "whsec_placeholder",
    "stripe_price_id": "price_placeholder",
    "app_base_url": "http://localhost:3000",
}


def make_settings(**twilio) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **STRIPE,
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
        **twilio,
    )


def test_twilio_is_optional_and_off_by_default(monkeypatch):
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
        monkeypatch.delenv(name, raising=False)
    assert make_settings().twilio_configured is False


def test_configured_only_when_all_three_are_set():
    full = {
        "twilio_account_sid": SID,
        "twilio_auth_token": TOKEN,
        "twilio_from_number": "+15005550006",
    }
    assert make_settings(**full).twilio_configured is True
    for missing in full:
        partial = {k: v for k, v in full.items() if k != missing}
        assert make_settings(**partial).twilio_configured is False


@pytest.mark.parametrize(
    "bad",
    [
        {"twilio_account_sid": "not-a-sid"},
        {"twilio_from_number": "5005550006"},
        {"twilio_from_number": "+0123456789"},
        {"api_public_base_url": "ftp://x"},
        {"twilio_timeout_seconds": 0},
    ],
    ids=["sid", "from-no-plus", "from-leading-zero", "public-url", "timeout"],
)
def test_bad_values_fail_at_startup(bad):
    with pytest.raises(ValidationError):
        make_settings(**bad)


def test_blank_values_count_as_unset_and_public_url_is_normalized():
    s = make_settings(
        twilio_account_sid="", twilio_from_number=" ", api_public_base_url=f"{PUBLIC}/"
    )
    assert s.twilio_account_sid is None and s.twilio_from_number is None
    assert s.api_public_base_url == PUBLIC


def test_callback_url(monkeypatch):
    monkeypatch.setattr(settings, "api_public_base_url", PUBLIC)
    assert twilio_deps.sms_status_callback_url() == f"{PUBLIC}/api/v1/webhooks/twilio/sms-status"
    monkeypatch.setattr(settings, "api_public_base_url", None)
    assert twilio_deps.sms_status_callback_url() is None


def test_notifier_is_demo_without_an_api_and_twilio_with_one():
    assert isinstance(twilio_deps.get_notifier(guard(), None), LogNotifier)
    assert isinstance(twilio_deps.get_notifier(guard(), FakeSmsApi()), TwilioSmsNotifier)


def test_get_sms_api_is_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", None)
    twilio_deps._build_api.cache_clear()
    try:
        assert twilio_deps.get_sms_api() is None
    finally:
        twilio_deps._build_api.cache_clear()


# --- TwilioWebhookAuth on a tiny app ---------------------------------------------------

AUTH = twilio_deps.TwilioWebhookAuth()
app = FastAPI()
register_exception_handlers(app)


@app.post("/hook")
async def hook(fields: dict[str, str] = Depends(AUTH)):
    return fields


client = TestClient(app, base_url="http://internal.test")
FORM = {"MessageSid": "SM1", "MessageStatus": "delivered", "ErrorCode": ""}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", SID)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "twilio_from_number", "+15005550006")
    monkeypatch.setattr(settings, "api_public_base_url", PUBLIC)


def post(url_path: str = "/hook", signed_url: str | None = None, form=FORM, signature=None):
    sig = signature
    if sig is None:
        sig = compute_signature(TOKEN, signed_url or f"{PUBLIC}/hook", form)
    return client.post(
        url_path,
        content=urlencode(form),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": sig,
        },
    )


def test_valid_signature_returns_the_form(configured):
    response = post()
    assert response.status_code == 200
    assert response.json() == FORM


def test_query_string_is_part_of_the_signed_url(configured):
    ok = post("/hook?a=1", signed_url=f"{PUBLIC}/hook?a=1")
    assert ok.status_code == 200
    assert post("/hook?a=1").status_code == 403  # signed without the query


def test_uses_the_request_url_when_no_public_base_is_set(configured, monkeypatch):
    monkeypatch.setattr(settings, "api_public_base_url", None)
    assert post(signed_url="http://internal.test/hook").status_code == 200


def test_bad_or_missing_signature_is_403_envelope(configured):
    bad = post(signature="AAAA")
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "invalid_signature"

    missing = client.post(
        "/hook",
        content=urlencode(FORM),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "invalid_signature"


def test_tampered_form_is_rejected(configured):
    sig = compute_signature(TOKEN, f"{PUBLIC}/hook", FORM)
    assert post(form={**FORM, "MessageStatus": "failed"}, signature=sig).status_code == 403


def test_unconfigured_twilio_rejects_everything(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", None)
    monkeypatch.setattr(settings, "twilio_auth_token", None)
    monkeypatch.setattr(settings, "twilio_from_number", None)
    response = post(signature=compute_signature(TOKEN, f"{PUBLIC}/hook", FORM))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_signature"


def test_repeated_form_keys_are_signed_in_full_and_last_value_is_returned(configured):
    body = "Tag=a&MessageSid=SM1&Tag=b"
    sig = compute_signature(
        TOKEN, f"{PUBLIC}/hook", [("Tag", "a"), ("MessageSid", "SM1"), ("Tag", "b")]
    )
    response = client.post(
        "/hook",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded", "X-Twilio-Signature": sig},
    )
    assert response.status_code == 200
    assert response.json() == {"Tag": "b", "MessageSid": "SM1"}
    # Signed over a collapsed dict (only the last value): must NOT validate.
    collapsed = compute_signature(TOKEN, f"{PUBLIC}/hook", {"Tag": "b", "MessageSid": "SM1"})
    bad = client.post(
        "/hook",
        content=body,
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": collapsed,
        },
    )
    assert bad.status_code == 403


def test_oversized_bodies_are_413(configured):
    headers = {"content-type": "application/x-www-form-urlencoded", "X-Twilio-Signature": "x"}
    big = "a=" + "x" * (twilio_deps.MAX_WEBHOOK_BODY_BYTES + 1)
    declared = client.post("/hook", content=big, headers=headers)
    assert declared.status_code == 413
    assert declared.json()["error"]["code"] == "payload_too_large"

    def chunks():  # no Content-Length: chunked
        for _ in range(3):
            yield b"a=" + b"x" * (30 * 1024)

    streamed = client.post("/hook", content=chunks(), headers=headers)
    assert streamed.status_code == 413
    assert streamed.json()["error"]["code"] == "payload_too_large"
