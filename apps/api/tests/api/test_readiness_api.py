"""Slice 16 over HTTP on real Postgres: the activation checklist (`/agent/readiness`,
`/agent/activate`, `/agent/deactivate`), what an inactive agent does to a phone call and to
the test chat, the integration status page, and the dashboard summary route."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from core.db import async_session_factory
from integrations.llm.fake import agent_turn
from models.agent import Agent
from models.escalation import Escalation
from models.phone_number import PhoneNumber
from services.conversation import INVALID_OUTPUT_FALLBACK
from tests.api.inbox_factory import add_interaction
from tests.api.test_bookings import NOW, _connect, _salon
from tests.api.test_voice import (  # noqa: F401  (fixtures used by name)
    _associate,
    _auth,
    _call_sid,
    _start,
    client,
    numbers,
    twilio_configured,
    voice,
)

# The slice-14 fixtures (`client`, `numbers`, ...) are imported by name, which pytest needs
# and ruff reads as redefinitions.
# ruff: noqa: F811

READINESS = "/api/v1/agent/readiness"
KEYS = ["persona", "services", "hours", "calendar", "escalation_contact", "phone", "test_chat"]
CONTACT = {
    "contact_phone": "+15551234567",
    "sms_enabled": False,
    "clarification_threshold": 2,
    "urgent_keywords": [],
}


def _items(body: dict) -> dict[str, dict]:
    return {item["key"]: item for item in body["items"]}


async def _chat(client, tenant, llm, message="What do you do?", interaction_id=None):
    llm.queue(agent_turn("We cut hair, happy to help."))
    response = await client.post(
        "/api/v1/agent/test",
        json={"interaction_id": interaction_id, "message": message},
        headers=_auth(tenant),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _ready_up(client, tenant, numbers, calendar, llm) -> tuple[str, str]:
    """Everything the checklist asks for; returns the associated number (E.164) and the
    calendar's refresh token."""
    await _salon(client, tenant)  # an active service and opening hours
    refresh = await _connect(client, tenant, calendar)
    saved = await client.put(
        "/api/v1/business/escalation-settings", json=CONTACT, headers=_auth(tenant)
    )
    assert saved.status_code == 200, saved.text
    _, to = await _associate(client, tenant, numbers, activate=False)
    await _chat(client, tenant, llm)
    return to, refresh


async def _readiness(client, tenant) -> dict:
    response = await client.get(READINESS, headers=_auth(tenant))
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_new_agent_is_inactive_and_not_ready(client, entitled_tenants):
    tenant, _ = entitled_tenants

    body = await _readiness(client, tenant)

    assert body["active"] is False and body["ready"] is False
    assert [item["key"] for item in body["items"]] == KEYS
    items = _items(body)
    assert all(item["required"] is True for item in body["items"])
    # The default name and greeting are set; nothing else is.
    assert items["persona"]["ok"] is True
    assert items["persona"]["message"] == "Name and greeting are set."
    assert items["services"] == {
        "key": "services",
        "ok": False,
        "required": True,
        "message": "Add at least one active service.",
        "fix_url": "/dashboard/agent#services",
    }
    assert items["hours"]["message"] == "Set your opening hours."
    # No `calendar` fixture: Google Calendar is not configured on this server.
    assert items["calendar"]["message"] == "Google Calendar isn't available on this server yet."
    assert items["calendar"]["fix_url"] == "/dashboard/settings/calendar"
    assert items["escalation_contact"]["fix_url"] == "/dashboard/agent#escalation"
    assert items["phone"]["message"] == "Connect a phone number."
    assert items["phone"]["fix_url"] == "/dashboard/phone"
    assert items["test_chat"]["message"] == "Have a test conversation in the test chat."
    assert items["test_chat"]["fix_url"] == "/dashboard/agent#test-chat"
    agent = (await client.get("/api/v1/agent", headers=_auth(tenant))).json()
    assert agent["active"] is False


async def test_activation_is_blocked_with_409_until_every_item_passes(client, entitled_tenants):
    tenant, _ = entitled_tenants

    blocked = await client.post("/api/v1/agent/activate", headers=_auth(tenant))

    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "agent_not_ready"
    for named in ("an active service", "opening hours", "Google Calendar", "a phone number"):
        assert named in error["message"]
    assert (await client.get("/api/v1/agent", headers=_auth(tenant))).json()["active"] is False


async def test_the_whole_path_from_setup_to_a_live_call_and_back(
    client, entitled_tenants, calendar, numbers, llm
):
    tenant, _ = entitled_tenants
    headers = _auth(tenant)
    to, _ = await _ready_up(client, tenant, numbers, calendar, llm)

    # Ready, not yet active: callers hear the polite line and no interaction is created.
    body = await _readiness(client, tenant)
    assert body["ready"] is True and body["active"] is False
    assert all(item["ok"] for item in body["items"])
    items = _items(body)
    assert items["services"]["message"] == "1 active service."
    assert items["calendar"]["message"] == "Google Calendar is connected."
    assert items["phone"]["message"] == "A phone number is connected."
    before = await _start(client, to, _call_sid())
    assert before.hangs_up and "can't take calls on this line" in before.said

    # Activate (idempotent).
    activated = await client.post("/api/v1/agent/activate", headers=headers)
    again = await client.post("/api/v1/agent/activate", headers=headers)
    assert activated.status_code == 200, activated.text
    assert activated.json()["active"] is True and activated.json()["ready"] is True
    assert [item["key"] for item in activated.json()["items"]] == KEYS
    assert again.status_code == 200 and again.json() == activated.json()
    assert (await client.get("/api/v1/agent", headers=headers)).json()["active"] is True
    live = await _start(client, to, _call_sid())
    assert not live.hangs_up and live.gather_query is not None  # the greeting, then listening

    # Deactivate (idempotent): callers hear the polite line again.
    off = await client.post("/api/v1/agent/deactivate", headers=headers)
    off_again = await client.post("/api/v1/agent/deactivate", headers=headers)
    assert off.status_code == 200 and off.json()["active"] is False
    assert off.json()["ready"] is True  # still ready, just off
    assert off_again.status_code == 200 and off_again.json()["active"] is False
    after = await _start(client, to, _call_sid())
    assert after.hangs_up and "can't take calls on this line" in after.said


async def test_deactivate_always_works_even_when_nothing_is_ready(client, entitled_tenants):
    tenant, _ = entitled_tenants
    await client.get("/api/v1/agent", headers=_auth(tenant))
    async with async_session_factory() as session:
        await session.execute(
            update(Agent).where(Agent.business_id == tenant.business_id).values(active=True)
        )
        await session.commit()

    response = await client.post("/api/v1/agent/deactivate", headers=_auth(tenant))

    assert response.status_code == 200
    assert response.json()["active"] is False and response.json()["ready"] is False


async def test_a_lost_calendar_blocks_activation_again(
    client, entitled_tenants, calendar, numbers, llm
):
    tenant, _ = entitled_tenants
    _, refresh = await _ready_up(client, tenant, numbers, calendar, llm)
    calendar.config = replace(calendar.config, status_check_seconds=0)
    calendar.api.revoke_externally(refresh)  # the owner removes access at Google

    blocked = await client.post("/api/v1/agent/activate", headers=_auth(tenant))

    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "agent_not_ready"
    body = await _readiness(client, tenant)
    assert _items(body)["calendar"]["message"] == "Reconnect Google Calendar: access was lost."


async def test_activation_cannot_be_flipped_through_patch_agent(client, entitled_tenants):
    tenant, _ = entitled_tenants

    patched = await client.patch("/api/v1/agent", json={"active": True}, headers=_auth(tenant))

    assert patched.status_code == 200
    assert patched.json()["active"] is False


async def test_the_test_chat_works_while_the_agent_is_inactive(client, entitled_tenants, llm):
    tenant, _ = entitled_tenants
    assert (await _readiness(client, tenant))["active"] is False

    turn = await _chat(client, tenant, llm, "Do you do beard trims?")

    assert turn["reply_text"] == "We cut hair, happy to help."
    assert turn["fallback"] is False
    assert (await _readiness(client, tenant))["active"] is False  # chatting never activates


async def test_only_a_real_reply_to_a_customer_counts_as_a_test_chat(client, entitled_tenants, llm):
    tenant_a, tenant_b = entitled_tenants

    # A greeting alone (no customer message) is not a conversation.
    await add_interaction(
        tenant_a.business_id, messages=(("agent", "Hi, thanks for calling!"),), status="in_progress"
    )
    # Neither is a voice call: only the test chat counts.
    await add_interaction(tenant_a.business_id, channel="voice")
    assert _items(await _readiness(client, tenant_a))["test_chat"]["ok"] is False

    # Another business's test chat is not ours.
    await _chat(client, tenant_b, llm)
    assert _items(await _readiness(client, tenant_a))["test_chat"]["ok"] is False
    assert _items(await _readiness(client, tenant_b))["test_chat"]["ok"] is True

    # A fallback reply (the model answered garbage twice) does not count...
    llm.queue("garbage", "still garbage")
    first = await client.post(
        "/api/v1/agent/test", json={"message": "hello"}, headers=_auth(tenant_a)
    )
    assert first.json()["reply_text"] == INVALID_OUTPUT_FALLBACK and first.json()["fallback"]
    assert _items(await _readiness(client, tenant_a))["test_chat"]["ok"] is False

    # ...a real reply in the same conversation does.
    await _chat(client, tenant_a, llm, "Are you open?", first.json()["interaction_id"])
    assert _items(await _readiness(client, tenant_a))["test_chat"]["ok"] is True


async def test_the_checklist_routes_need_a_plan(client, two_tenants):
    tenant, _ = two_tenants  # onboarded, no subscription
    headers = _auth(tenant)

    responses = [
        await client.get(READINESS, headers=headers),
        await client.post("/api/v1/agent/activate", headers=headers),
        await client.post("/api/v1/agent/deactivate", headers=headers),
        await client.get("/api/v1/integrations/status", headers=headers),
        await client.get("/api/v1/dashboard/summary", headers=headers),
        await client.request(
            "DELETE",
            "/api/v1/business/test-data",
            json={"confirmation": "DELETE TEST DATA"},
            headers=headers,
        ),
    ]

    assert [r.status_code for r in responses] == [402] * 6
    assert {r.json()["error"]["code"] for r in responses} == {"subscription_required"}


async def test_readiness_is_per_business(client, entitled_tenants, calendar):
    tenant_a, tenant_b = entitled_tenants
    await _salon(client, tenant_a)

    assert _items(await _readiness(client, tenant_a))["services"]["ok"] is True
    assert _items(await _readiness(client, tenant_b))["services"]["ok"] is False


# --- integration status ------------------------------------------------------------------


STATUS = "/api/v1/integrations/status"


async def test_integration_status_for_a_new_business(client, entitled_tenants):
    tenant, _ = entitled_tenants

    response = await client.get(STATUS, headers=_auth(tenant))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"calendar", "phone", "agent", "readiness", "errors", "usage"}
    assert body["calendar"] == {
        "configured": False,
        "status": "not_connected",
        "calendar_name": None,
        "last_error": None,
        "last_synced_at": None,
    }
    assert body["phone"] == {
        "configured": True,  # the `client` fixture configures Twilio
        "status": "not_connected",
        "phone_number": None,
        "last_error": None,
        "verified_at": None,
    }
    assert body["agent"] == {"active": False, "ready": False}
    assert body["readiness"]["ready"] is False and body["readiness"]["active"] is False
    assert [item["key"] for item in body["readiness"]["items"]] == KEYS
    assert body["errors"] == []
    # The usage block is exactly what GET /usage returns.
    assert body["usage"] == (await client.get("/api/v1/usage", headers=_auth(tenant))).json()


async def test_integration_status_reports_a_lost_calendar_in_errors(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    refresh = await _connect(client, tenant, calendar)
    calendar.config = replace(calendar.config, status_check_seconds=0)
    calendar.api.revoke_externally(refresh)
    refreshes_before = len(calendar.api.calls_to("refresh_access_token"))

    response = await client.get(STATUS, headers=_auth(tenant))

    body = response.json()
    assert body["calendar"]["configured"] is True
    assert body["calendar"]["status"] == "reconnect_needed"
    assert body["calendar"]["last_error"] == "auth_revoked"
    assert body["calendar"]["calendar_name"] == "Work"
    [error] = body["errors"]
    assert (error["source"], error["code"]) == ("calendar", "auth_revoked")
    datetime.fromisoformat(error["occurred_at"].replace("Z", "+00:00"))
    # The same status feeds the checklist, and Google was probed once for the whole page.
    assert _items(body["readiness"])["calendar"]["ok"] is False
    assert len(calendar.api.calls_to("refresh_access_token")) == refreshes_before + 1


async def test_integration_status_shows_the_phone_and_every_error_source_newest_first(
    client, entitled_tenants, numbers
):
    tenant, other = entitled_tenants
    business_id = tenant.business_id
    _, connected = await _associate(client, tenant, numbers, activate=False)
    now = NOW  # the pinned clock the page measures "the last seven days" against
    interaction = await add_interaction(business_id)
    other_interaction = await add_interaction(other.business_id)
    async with async_session_factory() as session:
        number = (
            await session.scalars(select(PhoneNumber).where(PhoneNumber.business_id == business_id))
        ).one()
        # A second, broken number. The connected one is the best one to show.
        session.add(
            PhoneNumber(
                id=uuid.uuid4(),
                business_id=business_id,
                phone_number="+15559990000",
                provider_number_sid="PN" + uuid.uuid4().hex,
                status="misconfigured",
                last_error="voice_url_mismatch",
                updated_at=now - timedelta(hours=2),
            )
        )
        number.last_error = "unavailable"
        number.updated_at = now - timedelta(hours=1)
        for reason, status, code, age in (
            ("urgent", "failed", "30007", timedelta(hours=3)),
            ("complaint", "undelivered", None, timedelta(minutes=30)),
            ("human_requested", "delivered", None, timedelta(minutes=1)),  # worked: not an error
        ):
            session.add(
                Escalation(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    interaction_id=interaction,
                    reason=reason,
                    urgency="normal",
                    status="open",
                    sms_status=status,
                    sms_error=code,
                    updated_at=now - age,
                )
            )
        # Too old (over seven days), and another business's failure: neither shows.
        session.add(
            Escalation(
                id=uuid.uuid4(),
                business_id=business_id,
                interaction_id=await add_interaction(business_id),
                reason="urgent",
                urgency="normal",
                status="open",
                sms_status="failed",
                sms_error="99999",
                updated_at=now - timedelta(days=8),
            )
        )
        session.add(
            Escalation(
                id=uuid.uuid4(),
                business_id=other.business_id,
                interaction_id=other_interaction,
                reason="urgent",
                urgency="normal",
                status="open",
                sms_status="failed",
                sms_error="88888",
            )
        )
        await session.commit()

    body = (await client.get(STATUS, headers=_auth(tenant))).json()

    assert body["phone"]["status"] == "connected"
    assert body["phone"]["phone_number"] == connected
    assert body["phone"]["last_error"] == "unavailable"
    assert [(e["source"], e["code"]) for e in body["errors"]] == [
        ("sms", "sms_failed"),  # 30 minutes ago, no Twilio code
        ("phone", "unavailable"),  # the connected number, an hour ago
        ("phone", "voice_url_mismatch"),  # the broken one, two hours ago
        ("sms", "30007"),  # three hours ago
    ]
    # Fixed codes only; never provider text.
    assert "88888" not in str(body) and "99999" not in str(body)


async def test_a_misconfigured_number_is_shown_when_none_is_connected(
    client, entitled_tenants, numbers
):
    tenant, _ = entitled_tenants
    await _associate(client, tenant, numbers, activate=False)
    async with async_session_factory() as session:
        await session.execute(
            update(PhoneNumber)
            .where(PhoneNumber.business_id == tenant.business_id)
            .values(status="misconfigured", last_error="number_missing")
        )
        await session.commit()

    body = (await client.get(STATUS, headers=_auth(tenant))).json()

    assert body["phone"]["status"] == "misconfigured"
    assert body["phone"]["last_error"] == "number_missing"
    assert _items(body["readiness"])["phone"]["message"] == (
        "Your phone number needs attention: test it on the Phone page."
    )
    assert [(e["source"], e["code"]) for e in body["errors"]] == [("phone", "number_missing")]


async def test_integration_status_is_per_business(client, entitled_tenants, numbers):
    tenant_a, tenant_b = entitled_tenants
    await _associate(client, tenant_a, numbers, activate=False)

    mine = (await client.get(STATUS, headers=_auth(tenant_a))).json()
    theirs = (await client.get(STATUS, headers=_auth(tenant_b))).json()

    assert mine["phone"]["status"] == "connected"
    assert theirs["phone"]["status"] == "not_connected" and theirs["phone"]["phone_number"] is None


# --- dashboard summary -------------------------------------------------------------------


SUMMARY = "/api/v1/dashboard/summary"


async def test_the_summary_counts_the_businesss_records(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    now = NOW  # the pinned clock: the default range ends on its local date
    for _ in range(2):
        await add_interaction(tenant_a.business_id, started_at=now - timedelta(days=1))
    await add_interaction(tenant_a.business_id, started_at=now - timedelta(days=60))
    await add_interaction(tenant_b.business_id, started_at=now - timedelta(days=1))

    response = await client.get(SUMMARY, headers=_auth(tenant_a))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "from",
        "to",
        "timezone",
        "total_interactions",
        "successful_bookings",
        "faqs_answered",
        "open_escalations",
    }
    assert body["timezone"] == "America/New_York"
    assert (body["total_interactions"], body["faqs_answered"]) == (2, 2)
    assert (body["successful_bookings"], body["open_escalations"]) == (0, 0)
    assert (datetime.fromisoformat(body["to"]) - datetime.fromisoformat(body["from"])).days == 29


async def test_the_summary_takes_from_and_to(client, entitled_tenants):
    tenant, _ = entitled_tenants
    await add_interaction(tenant.business_id, started_at=datetime(2026, 10, 20, 15, tzinfo=UTC))
    await add_interaction(tenant.business_id, started_at=datetime(2026, 10, 22, 15, tzinfo=UTC))

    both = await client.get(
        SUMMARY, params={"from": "2026-10-20", "to": "2026-10-21"}, headers=_auth(tenant)
    )
    only_to = await client.get(SUMMARY, params={"to": "2026-10-22"}, headers=_auth(tenant))

    assert both.json()["from"] == "2026-10-20" and both.json()["to"] == "2026-10-21"
    assert both.json()["total_interactions"] == 1
    assert only_to.json()["to"] == "2026-10-22"
    assert only_to.json()["from"] == "2026-09-23"
    assert only_to.json()["total_interactions"] == 2


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"from": "2026-10-22", "to": "2026-10-20"}, id="from-after-to"),
        pytest.param({"from": "2024-01-01", "to": "2026-10-20"}, id="over-a-year"),
        pytest.param({"from": "2999-01-01"}, id="from-after-the-default-to"),
    ],
)
async def test_a_bad_range_is_422_invalid_date_range(client, entitled_tenants, params):
    tenant, _ = entitled_tenants
    response = await client.get(SUMMARY, params=params, headers=_auth(tenant))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_date_range"


async def test_a_malformed_date_is_a_validation_error(client, entitled_tenants):
    tenant, _ = entitled_tenants

    response = await client.get(SUMMARY, params={"from": "yesterday"}, headers=_auth(tenant))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
