"""Every provider webhook rejects an unsigned or wrongly signed request.

The Twilio routes are DISCOVERED from the route table (`isolation.webhook_routes`), so a new
webhook under `/api/v1/webhooks/` is covered the day it is added, and the isolation sweep's
allowlist is cross-checked against the same discovery.
"""

import uuid
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from core.config import settings
from integrations.twilio.signature import compute_signature
from tests.security.isolation import webhook_routes

PUBLIC = "https://api.example.com"
TOKEN = "webhook-sweep-token"
TWILIO_PREFIX = "/api/v1/webhooks/twilio/"


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def twilio_configured(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "c" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "twilio_from_number", "+15005550006")
    monkeypatch.setattr(settings, "api_public_base_url", PUBLIC)


def twilio_paths(app) -> list[str]:
    return [spec.path for spec in webhook_routes(app) if spec.path.startswith(TWILIO_PREFIX)]


def form_for(path: str) -> dict[str, str]:
    return {
        "CallSid": f"CA{uuid.uuid4().hex}",
        "MessageSid": f"SM{uuid.uuid4().hex}",
        "MessageStatus": "delivered",
        "CallStatus": "completed",
        "From": "+15551230000",
        "To": "+15551239999",
    }


def post(client: AsyncClient, path: str, form: dict[str, str], signature: str | None):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if signature is not None:
        headers["X-Twilio-Signature"] = signature
    return client.post(path, content=urlencode(form), headers=headers)


def test_the_sweep_finds_the_known_twilio_webhooks(app):
    assert set(twilio_paths(app)) >= {
        "/api/v1/webhooks/twilio/voice",
        "/api/v1/webhooks/twilio/input",
        "/api/v1/webhooks/twilio/input/wait",
        "/api/v1/webhooks/twilio/status",
        "/api/v1/webhooks/twilio/sms-status",
    }


async def test_every_twilio_webhook_rejects_missing_and_forged_signatures(
    app, client, twilio_configured
):
    paths = twilio_paths(app)
    assert paths
    for path in paths:
        form = form_for(path)
        url = f"{PUBLIC}{path}"
        unsigned = await post(client, path, form, None)
        garbage = await post(client, path, form, "not-a-signature")
        wrong_token = await post(client, path, form, compute_signature("other-token", url, form))
        wrong_url = await post(
            client, path, form, compute_signature(TOKEN, f"{PUBLIC}/elsewhere", form)
        )
        tampered = await post(
            client, path, {**form, "From": "+15559999999"}, compute_signature(TOKEN, url, form)
        )
        for name, response in (
            ("unsigned", unsigned),
            ("garbage", garbage),
            ("wrong token", wrong_token),
            ("wrong url", wrong_url),
            ("tampered form", tampered),
        ):
            assert response.status_code == 403, f"{path} ({name}): {response.text}"
            assert response.json()["error"]["code"] == "invalid_signature", f"{path} ({name})"


async def test_every_twilio_webhook_rejects_everything_when_twilio_is_not_configured(
    app, client, monkeypatch
):
    monkeypatch.setattr(settings, "twilio_auth_token", None)
    monkeypatch.setattr(settings, "twilio_account_sid", None)
    monkeypatch.setattr(settings, "twilio_from_number", None)
    for path in twilio_paths(app):
        form = form_for(path)
        signed_anyway = compute_signature(TOKEN, f"{PUBLIC}{path}", form)
        for signature in (None, signed_anyway):
            response = await post(client, path, form, signature)
            assert response.status_code == 403, path
            assert response.json()["error"]["code"] == "invalid_signature", path


@pytest.mark.parametrize("signature", [None, "", "t=1,v1=deadbeef", "whsec_guess"])
async def test_the_stripe_webhook_rejects_a_missing_or_bad_signature(client, signature):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Stripe-Signature"] = signature
    payload = b'{"id": "evt_forged", "type": "customer.subscription.deleted"}'

    response = await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_webhook"
