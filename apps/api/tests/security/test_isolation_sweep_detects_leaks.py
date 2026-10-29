"""Proof that the sweep FAILS on a broken endpoint: an app with deliberately leaky routes
must produce findings for each of them, and only for them."""

import uuid
from typing import Any

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from core.tenant import Tenant, get_tenant
from core.voice import get_numbers_api
from integrations.twilio.fake import FakeNumbersApi
from models.faq import Faq
from tests.security.isolation import CASES, Case, seed_tenant, sweep


class Widget(BaseModel):
    name: str


def add_broken_routes(app: FastAPI) -> None:
    @app.get("/api/v1/_leaky/faqs/{faq_id}")
    async def leaky_faq(
        faq_id: uuid.UUID,
        tenant: Tenant = Depends(get_tenant),
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        # BROKEN on purpose: loads by id with no business_id scope.
        faq = (await session.execute(select(Faq).where(Faq.id == faq_id))).scalar_one_or_none()
        return {"question": faq.question if faq else None}

    @app.get("/api/v1/_leaky/faq-questions")
    async def leaky_collection(
        tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)
    ) -> dict[str, Any]:
        # BROKEN on purpose: every business's questions.
        rows = (await session.execute(select(Faq.question))).scalars().all()
        return {"questions": list(rows)}

    @app.post("/api/v1/_leaky/widgets/{widget_id}")
    async def unknown_param(
        widget_id: uuid.UUID, body: Widget, tenant: Tenant = Depends(get_tenant)
    ) -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/api/v1/_leaky/needs-body")
    async def needs_body(body: Widget, tenant: Tenant = Depends(get_tenant)) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/api/v1/_leaky/public")
    async def unclassified() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/api/v1/_leaky/no-auth-check/{faq_id}")
    async def forgot_auth(
        faq_id: uuid.UUID, session: AsyncSession = Depends(get_session)
    ) -> dict[str, Any]:
        return {}


async def test_the_sweep_reports_every_deliberately_broken_route(
    app, entitled_tenants, calendar, llm, billing_gateway, monkeypatch
):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "c" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr("sweep-token"))
    monkeypatch.setattr(settings, "twilio_from_number", "+15005550006")
    monkeypatch.setattr(settings, "api_public_base_url", "https://api.example.com")
    numbers = FakeNumbersApi(trial=True)
    app.dependency_overrides[get_numbers_api] = lambda: numbers
    add_broken_routes(app)
    tenant_a, tenant_b = entitled_tenants

    cases = {
        **CASES,
        # The leaky routes that have a case; `needs-body` and the widget route deliberately
        # have none, so the sweep must flag them.
        ("GET", "/api/v1/_leaky/faqs/{faq_id}"): Case(),
        ("GET", "/api/v1/_leaky/faq-questions"): Case(),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owner = await seed_tenant(
            client, tenant_a, llm=llm, billing_gateway=billing_gateway, calendar=calendar
        )
        intruder = await seed_tenant(
            client, tenant_b, llm=llm, billing_gateway=billing_gateway, calendar=calendar
        )
        numbers.add_number(owner.ids["phone_number"], sid=owner.ids["provider_number_sid"])
        findings = await sweep(client, app, owner, intruder, cases=cases)

    found = {(f.kind, f.path) for f in findings}
    # The leak by id: 200 instead of 404, with the owner's canary in the body.
    assert ("wrong-status", "/api/v1/_leaky/faqs/{faq_id}") in found
    assert ("leak", "/api/v1/_leaky/faqs/{faq_id}") in found
    # The leak through a collection.
    assert ("leak", "/api/v1/_leaky/faq-questions") in found
    # New endpoints are forced into coverage.
    assert ("unknown-param", "/api/v1/_leaky/widgets/{widget_id}") in found
    assert ("no-body-case", "/api/v1/_leaky/needs-body") in found
    assert ("unclassified", "/api/v1/_leaky/public") in found
    assert ("unclassified", "/api/v1/_leaky/no-auth-check/{faq_id}") in found

    # No false positives: every finding is about one of the broken routes.
    unexpected = [f for f in findings if "_leaky" not in f.path]
    assert unexpected == [], "\n".join(str(f) for f in unexpected)
