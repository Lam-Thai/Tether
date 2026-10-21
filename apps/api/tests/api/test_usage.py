"""Usage limits end to end: test chat -> UsageGuard -> Postgres -> GET /usage.

Gemini is faked (`llm`), so `len(llm.requests)` is the number of provider
calls made. Limits are tightened per test through the `usage` harness.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from integrations.llm.fake import agent_turn
from integrations.llm.port import LLMUnavailableError
from services.conversation import PROVIDER_FALLBACK
from services.usage import UsageKind

GEMINI = UsageKind.GEMINI_REQUEST


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _send(client, tenant, message="hello", interaction_id=None):
    response = await client.post(
        "/api/v1/agent/test",
        json={"interaction_id": interaction_id, "message": message},
        headers=_auth(tenant),
    )
    return response


async def _meter(client, tenant, kind=GEMINI):
    response = await client.get("/api/v1/usage", headers=_auth(tenant))
    assert response.status_code == 200, response.text
    body = response.json()
    return body, next(m for m in body["meters"] if m["kind"] == kind.value)


async def test_business_limit_falls_back_with_zero_model_calls_for_that_tenant_only(
    client, entitled_tenants, llm, usage
):
    tenant_a, tenant_b = entitled_tenants
    usage.set_daily(GEMINI, per_day=1_000, per_business_per_day=3)
    llm.queue(*[agent_turn(f"reply {i}") for i in range(4)])

    first = (await _send(client, tenant_a, "one")).json()
    for text in ("two", "three"):
        await _send(client, tenant_a, text, first["interaction_id"])
    fourth = await _send(client, tenant_a, "four", first["interaction_id"])

    assert fourth.status_code == 200, fourth.text
    body = fourth.json()
    assert body["fallback"] is True and body["usage_limited"] is True
    assert body["reply_text"].endswith("directly and someone will help you.")
    assert len(llm.requests) == 3  # the 4th message made no Gemini call

    # Another tenant is unaffected by tenant A's per-business limit.
    other = await _send(client, tenant_b, "hi")
    assert other.json()["usage_limited"] is False
    assert len(llm.requests) == 4

    # The panel reads it back from the DB, scoped to each tenant.
    summary, meter = await _meter(client, tenant_a)
    assert (meter["used"], meter["limit"], meter["global_limit_reached"]) == (3, 3, False)
    assert summary["gemini_tokens"] == 3 * 120
    assert summary["window"]["timezone"] == "UTC"
    assert summary["window"]["starts_at"].startswith(usage.day.date().isoformat())
    assert summary["concurrency"]["active"] == 1
    assert float(summary["estimated_cost_usd"]) > 0
    _, theirs = await _meter(client, tenant_b)
    assert theirs["used"] == 1

    # The transcript keeps the fallback marked as one after a reload.
    transcript = await client.get(
        f"/api/v1/agent/test/{first['interaction_id']}", headers=_auth(tenant_a)
    )
    assert transcript.json()["messages"][-1]["fallback"] is True


async def test_global_limit_exhaustion_affects_every_tenant(client, entitled_tenants, llm, usage):
    tenant_a, tenant_b = entitled_tenants
    usage.set_daily(GEMINI, per_day=2, per_business_per_day=1_000)
    llm.queue(agent_turn("a1"), agent_turn("a2"))

    first = (await _send(client, tenant_a, "one")).json()
    await _send(client, tenant_a, "two", first["interaction_id"])
    third = (await _send(client, tenant_a, "three", first["interaction_id"])).json()
    other = (await _send(client, tenant_b, "hi")).json()

    assert third["usage_limited"] is True and other["usage_limited"] is True
    assert len(llm.requests) == 2
    for tenant, used in ((tenant_a, 2), (tenant_b, 0)):
        _, meter = await _meter(client, tenant)
        assert meter["used"] == used  # each tenant sees only its own numbers
        assert meter["global_limit_reached"] is True


async def test_failed_calls_are_retried_per_policy_and_counted_and_new_chats_are_admitted(
    client, entitled_tenants, llm, usage, monkeypatch
):
    tenant_a, tenant_b = entitled_tenants
    monkeypatch.setattr("asyncio.sleep", _no_sleep)  # skip the retry backoff
    usage.set_concurrency(total=1_000, per_business=1)
    flaky = LLMUnavailableError("503", retryable=True)
    llm.queue(flaky, flaky, agent_turn("b ok"))

    failed = (await _send(client, tenant_a, "hi")).json()
    assert failed["reply_text"] == PROVIDER_FALLBACK
    assert len(llm.requests) == 2  # 2 attempts, then the fallback: never more

    summary, meter = await _meter(client, tenant_a)
    assert meter["used"] == 2 and summary["failed_requests"] == 2

    # Tenant A's one concurrent chat is open, so a second new chat is refused...
    refused = await _send(client, tenant_a, "another chat")
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "concurrency_limited"
    # ...while tenant B can still start one, and A can keep using the open one.
    assert (await _send(client, tenant_b, "hi")).status_code == 200
    llm.queue(agent_turn("a again"))
    again = await _send(client, tenant_a, "still there?", failed["interaction_id"])
    assert again.status_code == 200, again.text


async def test_usage_requires_a_signed_in_entitled_owner(client, two_tenants):
    assert (await client.get("/api/v1/usage")).status_code == 401
    unpaid = await client.get("/api/v1/usage", headers=_auth(two_tenants[0]))
    assert unpaid.status_code == 402
    assert unpaid.json()["error"]["code"] == "subscription_required"


async def _no_sleep(_seconds: float) -> None:
    return None
