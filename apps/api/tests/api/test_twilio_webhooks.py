"""`POST /api/v1/webhooks/twilio/sms-status` on real Postgres: the issue's demo from an
escalated chat to a `delivered` SMS, plus retries, reordering and bad signatures."""

import uuid
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from core.config import settings
from core.twilio import SMS_STATUS_PATH, get_sms_api
from integrations.llm.fake import agent_turn
from integrations.twilio.fake import FakeSmsApi
from integrations.twilio.signature import compute_signature

TOKEN = "webhook-test-token"
PUBLIC = "https://api.example.test"
SETTINGS = {
    "contact_phone": "+15551234567",
    "sms_enabled": True,
    "clarification_threshold": 2,
    "urgent_keywords": [],
}


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
def twilio_configured(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "c" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "twilio_from_number", "+15005550006")
    monkeypatch.setattr(settings, "api_public_base_url", PUBLIC)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _callback(client, form: dict[str, str], *, token: str = TOKEN):
    signature = compute_signature(token, f"{PUBLIC}{SMS_STATUS_PATH}", form)
    return await client.post(
        SMS_STATUS_PATH,
        content=urlencode(form),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )


async def _escalated_chat(client, app, llm, tenant) -> tuple[FakeSmsApi, str]:
    sms = FakeSmsApi(status="queued")
    app.dependency_overrides[get_sms_api] = lambda: sms
    put = await client.put(
        "/api/v1/business/escalation-settings", json=SETTINGS, headers=_auth(tenant)
    )
    assert put.status_code == 200, put.text
    llm.queue(
        agent_turn(
            "I'm sorry about that. I'll make sure the team is notified so they can review it.",
            intent="COMPLAINT",
            requires_human=True,
        )
    )
    turn = await client.post(
        "/api/v1/agent/test",
        json={"message": "I want to speak to a real person, my color came out wrong"},
        headers=_auth(tenant),
    )
    assert turn.status_code == 200, turn.text
    assert turn.json()["escalated"] is True
    return sms, turn.json()["interaction_id"]


async def _sms_status(client, tenant) -> tuple[str, str | None]:
    items = (await client.get("/api/v1/escalations", headers=_auth(tenant))).json()["items"]
    assert len(items) == 1
    return items[0]["sms_status"], items[0]["sms_error"]


async def test_the_demo_complaint_is_texted_once_and_the_status_reaches_delivered(
    client, app, llm, entitled_tenants, twilio_configured
):
    tenant, other = entitled_tenants
    sms, interaction_id = await _escalated_chat(client, app, llm, tenant)

    assert len(sms.sent) == 1
    sent = sms.sent[0]
    assert sent.to == SETTINGS["contact_phone"]
    assert sent.status_callback == f"{PUBLIC}{SMS_STATUS_PATH}"
    # No PII: a reason category and the inbox link only.
    assert sent.body.startswith("Tether alert: A customer needs help with ")
    assert sent.body.endswith(f"/dashboard/inbox/{interaction_id}")
    assert "color" not in sent.body
    assert await _sms_status(client, tenant) == ("queued", None)

    sid = sent.sid
    delivered = {"MessageSid": sid, "MessageStatus": "delivered", "ErrorCode": ""}
    for form in (
        {"MessageSid": sid, "MessageStatus": "sent"},
        delivered,
        delivered,  # Twilio retried the callback
        {"MessageSid": sid, "MessageStatus": "sending"},  # arrived late, out of order
    ):
        response = await _callback(client, form)
        assert response.status_code == 204, response.text
    assert await _sms_status(client, tenant) == ("delivered", None)

    # A callback for a message Tether never sent is acknowledged and ignored.
    unknown = await _callback(
        client, {"MessageSid": f"SM{uuid.uuid4().hex}", "MessageStatus": "failed"}
    )
    assert unknown.status_code == 204
    assert (await client.get("/api/v1/escalations", headers=_auth(other))).json()["items"] == []

    # Resolving it from the inbox.
    escalation_id = (await client.get("/api/v1/escalations", headers=_auth(tenant))).json()[
        "items"
    ][0]["id"]
    resolved = await client.patch(
        f"/api/v1/escalations/{escalation_id}", json={"status": "resolved"}, headers=_auth(tenant)
    )
    assert resolved.json()["status"] == "resolved"


async def test_a_failed_delivery_keeps_twilios_error_code(
    client, app, llm, entitled_tenants, twilio_configured
):
    tenant, _ = entitled_tenants
    sms, _ = await _escalated_chat(client, app, llm, tenant)
    form = {"MessageSid": sms.sent[0].sid, "MessageStatus": "undelivered", "ErrorCode": "30003"}
    assert (await _callback(client, form)).status_code == 204
    assert await _sms_status(client, tenant) == ("undelivered", "30003")


async def test_an_invalid_signature_is_403_and_changes_nothing(
    client, app, llm, entitled_tenants, twilio_configured
):
    tenant, _ = entitled_tenants
    sms, _ = await _escalated_chat(client, app, llm, tenant)
    form = {"MessageSid": sms.sent[0].sid, "MessageStatus": "delivered"}

    forged = await _callback(client, form, token="not-the-token")
    assert forged.status_code == 403
    assert forged.json()["error"]["code"] == "invalid_signature"
    missing = await client.post(
        SMS_STATUS_PATH,
        content=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert missing.status_code == 403
    assert await _sms_status(client, tenant) == ("queued", None)
