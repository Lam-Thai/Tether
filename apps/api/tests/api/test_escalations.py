"""`/api/v1/escalations`, escalation settings, and the engine -> inbox path, on real Postgres."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from core.db import async_session_factory
from core.twilio import get_sms_api
from integrations.llm.fake import agent_turn
from integrations.twilio.fake import FakeSmsApi
from models.escalation import Escalation
from tests.api.inbox_factory import add_interaction

URL = "/api/v1/escalations"
SETTINGS = "/api/v1/business/escalation-settings"
BODY = {
    "contact_phone": "+15551234567",
    "sms_enabled": True,
    "clarification_threshold": 3,
    "urgent_keywords": ["Gas Leak", "flood"],
}


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _add(business_id, interaction_id, reason="complaint", status="open") -> uuid.UUID:
    escalation_id = uuid.uuid4()
    async with async_session_factory() as session:
        session.add(
            Escalation(
                id=escalation_id,
                business_id=business_id,
                interaction_id=interaction_id,
                reason=reason,
                urgency="normal",
                status=status,
                sms_status="disabled",
            )
        )
        await session.commit()
    return escalation_id


async def test_list_filters_by_status_and_interaction_and_resolve_is_idempotent(
    client, entitled_tenants
):
    tenant_a, _ = entitled_tenants
    a = tenant_a.business_id
    first, second = await add_interaction(a), await add_interaction(a)
    open_id = await _add(a, first)
    await _add(a, second, reason="urgent", status="resolved")

    default = (await client.get(URL, headers=_auth(tenant_a))).json()["items"]
    assert [e["id"] for e in default] == [str(open_id)]
    assert set(default[0]) == {
        "id",
        "interaction_id",
        "reason",
        "urgency",
        "status",
        "sms_status",
        "sms_error",
        "created_at",
        "resolved_at",
    }
    everything = (await client.get(URL, params={"status": "all"}, headers=_auth(tenant_a))).json()
    assert len(everything["items"]) == 2
    one = (
        await client.get(
            URL, params={"status": "all", "interaction_id": str(second)}, headers=_auth(tenant_a)
        )
    ).json()["items"]
    assert [e["reason"] for e in one] == ["urgent"]

    resolved = await client.patch(
        f"{URL}/{open_id}", json={"status": "resolved"}, headers=_auth(tenant_a)
    )
    again = await client.patch(
        f"{URL}/{open_id}", json={"status": "resolved"}, headers=_auth(tenant_a)
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert again.status_code == 200 and again.json() == resolved.json()
    assert (await client.get(URL, headers=_auth(tenant_a))).json()["items"] == []


async def test_another_business_cannot_see_or_resolve_an_escalation(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    escalation_id = await _add(tenant_a.business_id, await add_interaction(tenant_a.business_id))

    seen = await client.get(URL, params={"status": "all"}, headers=_auth(tenant_b))
    patched = await client.patch(
        f"{URL}/{escalation_id}", json={"status": "resolved"}, headers=_auth(tenant_b)
    )

    assert seen.json()["items"] == []
    assert patched.status_code == 404
    assert patched.json()["error"]["code"] == "escalation_not_found"
    still = await client.get(URL, headers=_auth(tenant_a))
    assert len(still.json()["items"]) == 1


async def test_patch_rejects_any_other_body(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    escalation_id = await _add(tenant_a.business_id, await add_interaction(tenant_a.business_id))
    response = await client.patch(
        f"{URL}/{escalation_id}", json={"status": "open"}, headers=_auth(tenant_a)
    )
    assert response.status_code == 422


async def test_settings_defaults_then_put_get_and_isolation(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants

    defaults = (await client.get(SETTINGS, headers=_auth(tenant_a))).json()
    assert defaults == {
        "contact_phone": None,
        "sms_enabled": False,
        "clarification_threshold": 2,
        "urgent_keywords": [],
        "customized": False,
        "sms_configured": False,
    }
    saved = await client.put(SETTINGS, json=BODY, headers=_auth(tenant_a))
    assert saved.status_code == 200, saved.text
    assert saved.json()["urgent_keywords"] == ["gas leak", "flood"]
    assert saved.json()["customized"] is True
    assert (await client.get(SETTINGS, headers=_auth(tenant_a))).json() == saved.json()
    assert (await client.get(SETTINGS, headers=_auth(tenant_b))).json()["customized"] is False


async def test_sms_on_without_a_phone_is_422_contact_phone_required(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    response = await client.put(
        SETTINGS, json={**BODY, "contact_phone": None}, headers=_auth(tenant_a)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "contact_phone_required"


@pytest.mark.parametrize(
    "change",
    [
        {"contact_phone": "555-1234"},
        {"clarification_threshold": 0},
        {"clarification_threshold": 6},
        {"urgent_keywords": ["x" * 41]},
        {"urgent_keywords": [""]},
        {"urgent_keywords": [f"k{i}" for i in range(21)]},
        {"sms_enabled": "maybe"},
    ],
    ids=["phone", "thr-0", "thr-6", "kw-long", "kw-empty", "kw-21", "sms-type"],
)
async def test_settings_validation(client, entitled_tenants, change):
    tenant_a, _ = entitled_tenants
    response = await client.put(SETTINGS, json={**BODY, **change}, headers=_auth(tenant_a))
    assert response.status_code == 422


async def test_every_route_is_402_without_a_plan_and_401_without_a_token(client, two_tenants):
    tenant_a, _ = two_tenants
    calls = [
        client.get(URL, headers=_auth(tenant_a)),
        client.patch(f"{URL}/{uuid.uuid4()}", json={"status": "resolved"}, headers=_auth(tenant_a)),
        client.get(SETTINGS, headers=_auth(tenant_a)),
        client.put(SETTINGS, json=BODY, headers=_auth(tenant_a)),
    ]
    for call in calls:
        response = await call
        assert response.status_code == 402
        assert response.json()["error"]["code"] == "subscription_required"
    assert (await client.get(URL)).status_code == 401


async def test_a_chat_escalation_reaches_the_inbox_and_texts_the_owner_once(
    client, entitled_tenants, llm, app
):
    tenant_a, tenant_b = entitled_tenants
    sms = FakeSmsApi()
    app.dependency_overrides[get_sms_api] = lambda: sms
    await client.put(SETTINGS, json=BODY, headers=_auth(tenant_a))
    llm.queue(
        agent_turn("I will pass that on.", intent="HUMAN_REQUEST", requires_human=True),
        agent_turn("Your request has been noted.", intent="HUMAN_REQUEST"),
    )

    async def chat(message, interaction_id=None):
        response = await client.post(
            "/api/v1/agent/test",
            json={"interaction_id": interaction_id, "message": message},
            headers=_auth(tenant_a),
        )
        assert response.status_code == 200, response.text
        return response.json()

    first = await chat("I want to talk to a person")
    await chat("hello? a person please", first["interaction_id"])

    assert first["escalated"] is True
    assert len(sms.sent) == 1
    listed = (await client.get(URL, headers=_auth(tenant_a))).json()["items"]
    assert [(e["reason"], e["interaction_id"]) for e in listed] == [
        ("human_requested", first["interaction_id"])
    ]
    assert listed[0]["sms_status"] not in ("pending", "disabled", "skipped")
    assert (await client.get(URL, headers=_auth(tenant_b))).json()["items"] == []

    inbox = (await client.get("/api/v1/interactions", headers=_auth(tenant_a))).json()["items"]
    assert inbox[0]["needs_attention"] is True
    assert inbox[0]["escalation"]["reason"] == "human_requested"
    attention = (
        await client.get(
            "/api/v1/interactions", params={"type": "attention"}, headers=_auth(tenant_a)
        )
    ).json()["items"]
    assert [i["id"] for i in attention] == [first["interaction_id"]]
    detail = (
        await client.get(f"/api/v1/interactions/{first['interaction_id']}", headers=_auth(tenant_a))
    ).json()
    assert [e["reason"] for e in detail["escalations"]] == ["human_requested"]

    # Resolving clears "needs attention" but keeps the (resolved) summary.
    await client.patch(
        f"{URL}/{listed[0]['id']}", json={"status": "resolved"}, headers=_auth(tenant_a)
    )
    after = (await client.get("/api/v1/interactions", headers=_auth(tenant_a))).json()["items"][0]
    assert after["needs_attention"] is False and after["escalation"]["status"] == "resolved"
    gone = await client.get(
        "/api/v1/interactions", params={"type": "attention"}, headers=_auth(tenant_a)
    )
    assert gone.json()["items"] == []
