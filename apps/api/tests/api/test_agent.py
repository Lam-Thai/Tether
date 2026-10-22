"""Agent persona + test chat against a real Postgres, Gemini faked.

Covers what the engine's unit tests can't: the 402 gate on every route,
validation at the HTTP boundary, persistence and reload from Postgres,
cross-tenant isolation, and the interaction row lock under real concurrency.
"""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from core.db import async_session_factory
from integrations.llm.fake import agent_turn
from integrations.llm.port import LLMUnavailableError
from models.interaction import Interaction, InteractionMessage
from repositories import operations as operations_repo
from services.conversation import INVALID_OUTPUT_FALLBACK, PROVIDER_FALLBACK


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _send(client, tenant, message="hello", interaction_id=None):
    return await client.post(
        "/api/v1/agent/test",
        json={"interaction_id": interaction_id, "message": message},
        headers=_auth(tenant),
    )


async def test_get_agent_creates_the_defaults(client, entitled_tenants):
    tenant_a, _ = entitled_tenants

    response = await client.get("/api/v1/agent", headers=_auth(tenant_a))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Riley"
    assert body["tone"] == "friendly"
    assert body["greeting"] == "Hi, thanks for contacting Alpha Business! How can I help you today?"
    assert body["custom_instructions"] == ""
    assert body["active"] is True
    again = await client.get("/api/v1/agent", headers=_auth(tenant_a))
    assert again.json()["id"] == body["id"]


async def test_patch_agent_updates_only_the_fields_sent_and_strips(client, entitled_tenants):
    tenant_a, _ = entitled_tenants

    response = await client.patch(
        "/api/v1/agent",
        json={"name": "  Morgan  ", "tone": "concise", "custom_instructions": "  Be brief.  "},
        headers=_auth(tenant_a),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name"], body["tone"], body["custom_instructions"]) == (
        "Morgan",
        "concise",
        "Be brief.",
    )
    assert body["greeting"].startswith("Hi, thanks for contacting")  # untouched
    reread = await client.get("/api/v1/agent", headers=_auth(tenant_a))
    assert reread.json() == body


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "   "},
        {"name": "x" * 61},
        {"greeting": ""},
        {"greeting": "  "},
        {"greeting": "x" * 301},
        {"custom_instructions": "x" * 2001},
        {"tone": "sarcastic"},
        {"name": None},
    ],
)
async def test_patch_agent_validation_is_422(client, entitled_tenants, payload):
    response = await client.patch("/api/v1/agent", json=payload, headers=_auth(entitled_tenants[0]))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("message", ["", "   ", "x" * 1001])
async def test_test_turn_message_validation_is_422(client, entitled_tenants, message):
    response = await _send(client, entitled_tenants[0], message)

    assert response.status_code == 422


async def test_every_route_is_402_without_a_plan(client, two_tenants):
    tenant, _ = two_tenants  # onboarded, but never subscribed
    some_id = uuid.uuid4()
    calls = [
        ("GET", "/api/v1/agent", None),
        ("PATCH", "/api/v1/agent", {"name": "X"}),
        ("POST", "/api/v1/agent/test", {"message": "hi"}),
        ("GET", f"/api/v1/agent/test/{some_id}", None),
        ("POST", f"/api/v1/agent/test/{some_id}/end", None),
    ]
    for method, path, body in calls:
        response = await client.request(method, path, json=body, headers=_auth(tenant))
        assert response.status_code == 402, path
        assert response.json()["error"]["code"] == "subscription_required"


async def test_routes_require_a_bearer_token(client):
    response = await client.get("/api/v1/agent")
    assert response.status_code == 401


async def test_chat_round_trip_reloads_identically_from_postgres(client, entitled_tenants, llm):
    tenant_a, _ = entitled_tenants
    llm.queue(
        agent_turn("Let me find out for you.", intent="UNKNOWN"),
        agent_turn("Happy to help.", intent="UNKNOWN"),
    )

    first = await _send(client, tenant_a, "When do you open?")
    assert first.status_code == 200, first.text
    first_body = first.json()
    interaction_id = first_body["interaction_id"]
    assert [m["speaker"] for m in first_body["messages"]] == ["agent", "customer", "agent"]
    assert first_body["messages"][0]["text"].startswith("Hi, thanks for contacting")
    assert first_body["reply_text"] == "Let me find out for you."
    assert (first_body["intent"], first_body["status"]) == ("UNKNOWN", "active")
    assert first_body["ended"] is False and first_body["fallback"] is False

    second = await _send(client, tenant_a, "And Sunday?", interaction_id)
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert [m["sequence_number"] for m in second_body["messages"]] == [4, 5]

    reloaded = await client.get(f"/api/v1/agent/test/{interaction_id}", headers=_auth(tenant_a))
    assert reloaded.status_code == 200
    transcript = reloaded.json()
    assert transcript["interaction_id"] == interaction_id
    assert (transcript["status"], transcript["ended"], transcript["ended_at"]) == (
        "active",
        False,
        None,
    )
    assert transcript["messages"] == first_body["messages"] + second_body["messages"]
    assert [m["sequence_number"] for m in transcript["messages"]] == [1, 2, 3, 4, 5]
    # The second request's model call saw the persisted history.
    assert [m.text for m in llm.requests[1].messages][1:] == [
        "When do you open?",
        "Let me find out for you.",
        "And Sunday?",
    ]


async def test_persona_edit_applies_to_the_next_turn(client, entitled_tenants, llm):
    tenant_a, _ = entitled_tenants
    llm.queue(agent_turn("one"), agent_turn("two"))
    first = await _send(client, tenant_a, "hi")
    await client.patch("/api/v1/agent", json={"name": "Morgan"}, headers=_auth(tenant_a))

    await _send(client, tenant_a, "hi again", first.json()["interaction_id"])

    assert "Riley" in llm.requests[0].system_instruction
    assert "Morgan" in llm.requests[1].system_instruction


async def test_model_failures_show_the_fixed_fallbacks(client, entitled_tenants, llm):
    tenant_a, _ = entitled_tenants
    llm.queue("garbage", "still garbage", LLMUnavailableError("down"))

    invalid = await _send(client, tenant_a, "hi")
    unavailable = await _send(client, tenant_a, "hi", invalid.json()["interaction_id"])

    assert invalid.json()["reply_text"] == INVALID_OUTPUT_FALLBACK
    assert invalid.json()["fallback"] is True
    assert unavailable.json()["reply_text"] == PROVIDER_FALLBACK
    assert unavailable.json()["fallback"] is True
    assert len(llm.requests) == 3


async def test_end_is_idempotent_and_a_later_send_is_409(client, entitled_tenants, llm):
    tenant_a, _ = entitled_tenants
    llm.queue(agent_turn("hi"))
    interaction_id = (await _send(client, tenant_a)).json()["interaction_id"]
    url = f"/api/v1/agent/test/{interaction_id}/end"

    ended = await client.post(url, headers=_auth(tenant_a))
    again = await client.post(url, headers=_auth(tenant_a))

    assert ended.status_code == again.status_code == 200
    assert ended.json()["ended"] is True and ended.json()["status"] == "ended"
    assert again.json() == ended.json()
    late = await _send(client, tenant_a, "more", interaction_id)
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "interaction_ended"
    assert len(llm.requests) == 1


async def test_tenant_b_cannot_see_or_touch_tenant_a(client, entitled_tenants, llm):
    tenant_a, tenant_b = entitled_tenants
    llm.queue(agent_turn("hi"))
    await client.patch("/api/v1/agent", json={"name": "AlphaBot"}, headers=_auth(tenant_a))
    interaction_id = (await _send(client, tenant_a)).json()["interaction_id"]

    read = await client.get(f"/api/v1/agent/test/{interaction_id}", headers=_auth(tenant_b))
    send = await _send(client, tenant_b, "sneaky", interaction_id)
    end = await client.post(f"/api/v1/agent/test/{interaction_id}/end", headers=_auth(tenant_b))
    b_agent = await client.get("/api/v1/agent", headers=_auth(tenant_b))

    for response in (read, send, end):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "interaction_not_found"
    assert b_agent.json()["name"] == "Riley"  # B gets its own defaults, never A's agent
    assert len(llm.requests) == 1  # B's send never reached the model
    still_active = await client.get(f"/api/v1/agent/test/{interaction_id}", headers=_auth(tenant_a))
    assert still_active.json()["status"] == "active"
    assert len(still_active.json()["messages"]) == 3  # nothing from B was appended


async def test_parallel_new_chats_are_separate_interactions(client, entitled_tenants, llm):
    tenant_a, _ = entitled_tenants
    llm.queue(agent_turn("r1"), agent_turn("r2"))

    first, second = await asyncio.gather(
        _send(client, tenant_a, "one"), _send(client, tenant_a, "two")
    )

    assert first.status_code == second.status_code == 200
    a, b = first.json(), second.json()
    assert a["interaction_id"] != b["interaction_id"]
    for body in (a, b):
        assert [m["sequence_number"] for m in body["messages"]] == [1, 2, 3]
    # Both created the agent concurrently: exactly one persona row, same for both.
    agent = await client.get("/api/v1/agent", headers=_auth(tenant_a))
    assert agent.status_code == 200
    for body in (a, b):
        transcript = (
            await client.get(
                f"/api/v1/agent/test/{body['interaction_id']}", headers=_auth(tenant_a)
            )
        ).json()
        assert transcript["messages"] == body["messages"]


async def test_concurrent_double_send_never_duplicates_sequence_numbers(
    client, entitled_tenants, llm
):
    tenant_a, _ = entitled_tenants
    llm.queue(agent_turn("first"), *[agent_turn(f"r{i}") for i in range(4)])
    interaction_id = (await _send(client, tenant_a, "start")).json()["interaction_id"]

    results = await asyncio.gather(
        *[_send(client, tenant_a, f"burst {i}", interaction_id) for i in range(4)]
    )

    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    async with async_session_factory() as session:
        numbers = (
            (
                await session.execute(
                    select(InteractionMessage.sequence_number)
                    .where(InteractionMessage.interaction_id == uuid.UUID(interaction_id))
                    .order_by(InteractionMessage.sequence_number)
                )
            )
            .scalars()
            .all()
        )
        distinct = (
            await session.execute(
                select(func.count(func.distinct(InteractionMessage.sequence_number))).where(
                    InteractionMessage.interaction_id == uuid.UUID(interaction_id)
                )
            )
        ).scalar_one()
    assert numbers == list(range(1, 12))  # greeting + 2 + 4 * 2
    assert distinct == 11


async def test_fallback_flag_survives_reload_over_http(client, entitled_tenants, llm):
    tenant_a, _ = entitled_tenants
    llm.queue("bad", "bad")

    turn = (await _send(client, tenant_a, "hi")).json()
    reloaded = (
        await client.get(f"/api/v1/agent/test/{turn['interaction_id']}", headers=_auth(tenant_a))
    ).json()

    assert [m["fallback"] for m in turn["messages"]] == [False, False, True]
    assert reloaded["messages"] == turn["messages"]
    assert set(reloaded["messages"][0]) == {
        "sequence_number",
        "speaker",
        "text",
        "created_at",
        "fallback",
    }


async def test_new_chat_survives_an_unexpected_model_failure(app, entitled_tenants, llm):
    """The checkpoint commits the interaction and greeting before the model is called."""
    tenant_a, _ = entitled_tenants
    llm.queue(RuntimeError("boom"))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as c:
        response = await _send(c, tenant_a, "hi")

    assert response.status_code == 500
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(InteractionMessage.speaker, InteractionMessage.message_text)
                .join(Interaction, InteractionMessage.interaction_id == Interaction.id)
                .where(Interaction.business_id == tenant_a.business_id)
            )
        ).all()
    assert [r.speaker for r in rows] == ["agent"]
    assert rows[0].message_text.startswith("Hi, thanks for contacting")


async def test_model_unavailable_on_a_new_chat_returns_the_fallback_and_keeps_the_chat(
    client, entitled_tenants, llm
):
    tenant_a, _ = entitled_tenants
    llm.queue(LLMUnavailableError("down"))

    body = (await _send(client, tenant_a, "hi")).json()

    assert body["fallback"] is True and body["reply_text"] == PROVIDER_FALLBACK
    transcript = await client.get(
        f"/api/v1/agent/test/{body['interaction_id']}", headers=_auth(tenant_a)
    )
    assert len(transcript.json()["messages"]) == 3


async def test_interaction_busy_is_a_distinct_409_code(client, entitled_tenants, llm, monkeypatch):
    from repositories import interactions as interactions_repo

    tenant_a, _ = entitled_tenants
    monkeypatch.setattr(interactions_repo, "LOCK_TIMEOUT", "200ms")
    llm.queue(agent_turn("hi"), agent_turn("never reached"))
    interaction_id = (await _send(client, tenant_a)).json()["interaction_id"]

    async with async_session_factory() as holder:
        await interactions_repo.lock_interaction(
            holder, tenant_a.business_id, uuid.UUID(interaction_id), "test_chat"
        )
        busy = await _send(client, tenant_a, "blocked", interaction_id)
        await holder.rollback()

    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "interaction_busy"


def _search(query: str) -> dict:
    return agent_turn(
        "Let me check.", tool_calls=[{"name": "search_faq", "arguments": {"query": query}}]
    )


async def test_faq_edits_and_disabling_reach_the_very_next_reply(client, entitled_tenants, llm):
    """The slice's demo end to end: FAQ API -> test chat -> search_faq -> grounded reply."""
    tenant_a, tenant_b = entitled_tenants
    headers = _auth(tenant_a)
    created = await client.post(
        "/api/v1/faqs",
        json={
            "question": "Do you take walk-ins?",
            "answer": "Yes, before 3 PM.",
            "category": "Booking",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    faq_id = created.json()["id"]
    # Another tenant's FAQ on the same topic must never reach tenant A's prompt.
    other = await client.post(
        "/api/v1/faqs",
        json={"question": "Do you take walk-ins?", "answer": "Never, appointments only."},
        headers=_auth(tenant_b),
    )
    assert other.status_code == 201, other.text

    llm.queue(_search("walk in"), agent_turn("Yes, before 3 PM!", intent="FAQ", sources=[faq_id]))
    first = await _send(client, tenant_a, "can I just walk in?")
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["reply_text"], body["intent"], body["fallback"]) == (
        "Yes, before 3 PM!",
        "FAQ",
        False,
    )
    assert "Yes, before 3 PM." in llm.requests[1].system_instruction
    assert "Never, appointments only." not in llm.requests[1].system_instruction
    interaction_id = uuid.UUID(body["interaction_id"])

    edited = await client.patch(
        f"/api/v1/faqs/{faq_id}", json={"answer": "Yes, before 5 PM."}, headers=headers
    )
    assert edited.status_code == 200, edited.text
    llm.queue(_search("walk in"), agent_turn("Yes, before 5 PM!", intent="FAQ", sources=[faq_id]))
    second = await _send(client, tenant_a, "walk-ins today?", str(interaction_id))
    assert second.json()["reply_text"] == "Yes, before 5 PM!"
    assert "before 5 PM." in llm.requests[3].system_instruction
    assert "before 3 PM." not in llm.requests[3].system_instruction

    disabled = await client.patch(f"/api/v1/faqs/{faq_id}", json={"active": False}, headers=headers)
    assert disabled.status_code == 200, disabled.text
    # The model cites the now-disabled FAQ from memory: the guard must refuse it.
    llm.queue(_search("walk in"), agent_turn("Yes, before 5 PM!", intent="FAQ", sources=[faq_id]))
    third = await _send(client, tenant_a, "and tomorrow?", str(interaction_id))
    third_body = third.json()
    assert third_body["fallback"] is True
    assert third_body["reply_text"].startswith("Sorry, I don't have that information.")
    assert "before 5 PM" not in llm.requests[5].system_instruction

    # Every tool call is in the operation log, scoped to tenant A only.
    async with async_session_factory() as session:
        ops = await operations_repo.list_operations(session, tenant_a.business_id, interaction_id)
        foreign = await operations_repo.list_operations(
            session, tenant_b.business_id, interaction_id
        )
    assert [(o.tool_name, o.status) for o in ops] == [("search_faq", "succeeded")] * 3
    assert [o.result["faq_ids"] for o in ops] == [[faq_id], [faq_id], []]
    assert foreign == []
