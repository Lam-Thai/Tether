"""`/api/v1/calendar` against a real Postgres, with Google replaced by
`FakeGoogleCalendarApi` (the `calendar` fixture)."""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from core.db import async_session_factory
from integrations.google_calendar.fake import fake_calendar
from integrations.google_calendar.port import REQUIRED_SCOPES, GoogleApiError
from models.calendar import CalendarConnection
from models.usage import UsageEvent
from models.user import User
from services.calendar import token_context
from services.token_vault import SealedToken

OWNED = fake_calendar("work@example.com", "Work", primary=True)
SECOND = fake_calendar("side@example.com", "Side projects")
SHARED = fake_calendar("team@example.com", "Team", access_role="writer")
TRANSIENT = GoogleApiError("server_error", retryable=True)


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _state_from(client, tenant) -> str:
    response = await client.get("/api/v1/calendar/connect", headers=_auth(tenant))
    assert response.status_code == 200, response.text
    return parse_qs(urlparse(response.json()["url"]).query)["state"][0]


async def _connect(client, tenant, calendar, **grant):
    """Runs connect -> callback and returns (callback response, the refresh token issued)."""
    grant.setdefault("calendars", [OWNED, SECOND, SHARED])
    refresh = calendar.api.issue_grant(**grant)
    state = await _state_from(client, tenant)
    response = await client.post(
        "/api/v1/calendar/callback",
        json={"code": "auth-code-1", "state": state},
        headers=_auth(tenant),
    )
    return response, refresh


async def _row(tenant) -> CalendarConnection | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(CalendarConnection).where(CalendarConnection.business_id == tenant.business_id)
        )
        return result.scalar_one_or_none()


async def _connected(client, tenant, calendar, calendar_id: str = OWNED.id, **grant) -> str:
    response, refresh = await _connect(client, tenant, calendar, **grant)
    assert response.status_code == 200, response.text
    picked = await client.patch(
        "/api/v1/calendar", json={"calendar_id": calendar_id}, headers=_auth(tenant)
    )
    assert picked.status_code == 200, picked.text
    assert refresh is not None
    return refresh


async def test_connect_select_and_status_flow_keeps_the_token_secret(
    client, entitled_tenants, calendar, caplog
):
    caplog.set_level(logging.DEBUG)
    tenant, _ = entitled_tenants
    headers = _auth(tenant)
    bodies: list[str] = []

    url = (await client.get("/api/v1/calendar/connect", headers=headers)).json()["url"]
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert urlparse(url).netloc == "accounts.google.com"
    assert set(query["scope"].split()) == REQUIRED_SCOPES
    assert query["redirect_uri"] == calendar.config.redirect_uri
    assert (query["code_challenge_method"], query["access_type"], query["prompt"]) == (
        "S256",
        "offline",
        "consent",
    )

    before = await client.get("/api/v1/calendar/status", headers=headers)
    assert before.json() == {
        "status": "not_connected",
        "calendar_id": None,
        "calendar_name": None,
        "last_error": None,
        "last_synced_at": None,
    }

    callback, refresh = await _connect(client, tenant, calendar)
    assert callback.status_code == 200, callback.text
    assert callback.json()["status"] == "needs_selection"
    assert callback.json()["calendar_id"] is None
    bodies.append(callback.text)

    listed = await client.get("/api/v1/calendar/calendars", headers=headers)
    assert listed.status_code == 200
    # Only calendars the owner owns; the writer-only shared one is not offered.
    assert [c["id"] for c in listed.json()["calendars"]] == [OWNED.id, SECOND.id]
    assert set(listed.json()["calendars"][0]) == {"id", "name", "primary", "time_zone"}
    bodies.append(listed.text)

    not_owned = await client.patch(
        "/api/v1/calendar", json={"calendar_id": SHARED.id}, headers=headers
    )
    assert (not_owned.status_code, not_owned.json()["error"]["code"]) == (404, "calendar_not_found")

    picked = await client.patch(
        "/api/v1/calendar", json={"calendar_id": SECOND.id}, headers=headers
    )
    assert picked.status_code == 200, picked.text
    assert picked.json()["status"] == "connected"
    assert (picked.json()["calendar_id"], picked.json()["calendar_name"]) == (
        SECOND.id,
        "Side projects",
    )
    bodies.append(picked.text)

    probes_before = len(calendar.api.calls_to("list_calendars"))
    status = await client.get("/api/v1/calendar/status", headers=headers)
    assert status.json()["status"] == "connected"
    assert status.json()["last_synced_at"] is not None
    assert len(calendar.api.calls_to("list_calendars")) == probes_before  # fresh: no probe
    bodies.append(status.text)

    # At rest: ciphertext only, bound to this business, readable only with the key.
    row = await _row(tenant)
    assert row is not None and refresh is not None
    for column in CalendarConnection.__table__.columns:
        value = getattr(row, column.key)
        raw = value if isinstance(value, bytes) else str(value).encode()
        assert refresh.encode() not in raw, column.key
    assert (row.token_key_id, row.provider, row.connection_status) == (
        "test",
        "google",
        "connected",
    )
    sealed = SealedToken(row.token_key_id, bytes(row.encrypted_refresh_token))
    assert calendar.vault.decrypt(sealed, context=token_context(tenant.business_id)) == refresh

    # Every Google call was metered against this business.
    async with async_session_factory() as session:
        metered = await session.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.business_id == tenant.business_id,
                UsageEvent.event_type == "calendar_call",
            )
        )
    assert metered == len(calendar.api.calls)

    # In transit and in logs.
    secrets_ = [refresh, "auth-code-1", *(a for a in calendar.api.access_tokens)]
    for text in (*bodies, caplog.text):
        for secret in secrets_:
            assert secret not in text
    for text in bodies:
        assert "scope" not in text and "googleapis" not in text and '"test"' not in text


async def test_bad_state_is_rejected_and_nothing_is_stored(client, entitled_tenants, calendar):
    tenant_a, tenant_b = entitled_tenants
    calendar.api.issue_grant(calendars=[OWNED])
    state_a = await _state_from(client, tenant_a)

    def post(tenant, state):
        return client.post(
            "/api/v1/calendar/callback",
            json={"code": "auth-code-1", "state": state},
            headers=_auth(tenant),
        )

    user_a = await _user_id(tenant_a)
    expired_signer = calendar.signer(clock=lambda: datetime.now(UTC) - timedelta(hours=2))
    expired = expired_signer.issue(tenant_a.business_id, user_a).state
    wrong_secret = calendar.signer().issue(tenant_a.business_id, user_a).state[:-4] + "AAAA"

    for tenant, state in (
        (tenant_a, state_a[:-4] + "AAAA"),  # tampered
        (tenant_a, expired),
        (tenant_a, wrong_secret),
        (tenant_a, "not-a-state"),
        (tenant_b, state_a),  # started by A, replayed with B's bearer (login CSRF)
    ):
        response = await post(tenant, state)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_oauth_state"

    assert calendar.api.calls_to("exchange_code") == []
    assert await _row(tenant_a) is None and await _row(tenant_b) is None


async def test_rejected_grants_are_not_stored(client, entitled_tenants, calendar):
    tenant, _ = entitled_tenants
    headers = _auth(tenant)

    partial = REQUIRED_SCOPES - {next(iter(REQUIRED_SCOPES))}
    response, refresh = await _connect(client, tenant, calendar, scopes=partial)
    assert (response.status_code, response.json()["error"]["code"]) == (
        400,
        "calendar_scopes_missing",
    )
    assert calendar.api.revoked == set()  # a rejected grant is never revoked (grant-wide)

    response, _ = await _connect(client, tenant, calendar, refresh_token=None)
    assert (response.status_code, response.json()["error"]["code"]) == (
        400,
        "calendar_grant_incomplete",
    )

    calendar.api.fail_next("exchange_code", GoogleApiError("invalid_grant", auth_revoked=True))
    response, _ = await _connect(client, tenant, calendar)
    assert (response.status_code, response.json()["error"]["code"]) == (400, "invalid_oauth_state")

    exchanges = len(calendar.api.calls_to("exchange_code"))
    calendar.api.fail_next("exchange_code", TRANSIENT, times=3)
    response, _ = await _connect(client, tenant, calendar)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "calendar_unavailable")
    # A single-use code gets exactly one attempt, even for a retryable failure.
    assert len(calendar.api.calls_to("exchange_code")) == exchanges + 1

    assert await _row(tenant) is None
    status = await client.get("/api/v1/calendar/status", headers=headers)
    assert status.json()["status"] == "not_connected"


async def test_a_grant_revoked_at_google_needs_reconnect_and_reconnect_keeps_new_token(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    headers = _auth(tenant)
    first = await _connected(client, tenant, calendar, account="same-google-account")

    # Not stale: still reported connected without asking Google.
    assert (await client.get("/api/v1/calendar/status", headers=headers)).json()[
        "status"
    ] == "connected"

    calendar.api.revoke_externally(first)
    listing = await client.get("/api/v1/calendar/calendars", headers=headers)
    assert (listing.status_code, listing.json()["error"]["code"]) == (
        409,
        "calendar_reconnect_needed",
    )
    # The failed request still committed the new state.
    row = await _row(tenant)
    assert row is not None
    assert (row.connection_status, row.last_error) == ("reconnect_needed", "auth_revoked")
    status = (await client.get("/api/v1/calendar/status", headers=headers)).json()
    assert (status["status"], status["last_error"]) == ("reconnect_needed", "auth_revoked")
    picked = await client.patch("/api/v1/calendar", json={"calendar_id": OWNED.id}, headers=headers)
    assert picked.status_code == 409

    response, second = await _connect(client, tenant, calendar, account="same-google-account")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "needs_selection"  # the old choice does not carry over
    assert response.json()["last_error"] is None
    assert calendar.api.revoked == set()  # the old token is overwritten, never revoked
    assert (await client.get("/api/v1/calendar/calendars", headers=headers)).status_code == 200
    assert second is not None and second != first


async def test_stale_status_probes_google(client, entitled_tenants, calendar):
    tenant, _ = entitled_tenants
    headers = _auth(tenant)
    refresh = await _connected(client, tenant, calendar)
    calendar.config = replace(calendar.config, status_check_seconds=0)

    ok = (await client.get("/api/v1/calendar/status", headers=headers)).json()
    assert (ok["status"], ok["last_error"]) == ("connected", None)

    calendar.api.fail_next("list_calendars", TRANSIENT, times=2)
    outage = (await client.get("/api/v1/calendar/status", headers=headers)).json()
    assert (outage["status"], outage["last_error"]) == ("connected", "unavailable")

    assert refresh is not None
    calendar.api.set_calendars(refresh, [SECOND])  # the chosen calendar is gone
    missing = (await client.get("/api/v1/calendar/status", headers=headers)).json()
    assert (missing["status"], missing["last_error"], missing["calendar_id"]) == (
        "needs_selection",
        "calendar_missing",
        None,
    )

    calendar.api.revoke_externally(refresh)
    revoked = (await client.get("/api/v1/calendar/status", headers=headers)).json()
    assert (revoked["status"], revoked["last_error"]) == ("reconnect_needed", "auth_revoked")


async def test_calendar_outage_is_a_503_not_a_reconnect(client, entitled_tenants, calendar):
    tenant, _ = entitled_tenants
    await _connected(client, tenant, calendar)
    calendar.api.fail_next("list_calendars", TRANSIENT, times=2)

    response = await client.get("/api/v1/calendar/calendars", headers=_auth(tenant))

    assert (response.status_code, response.json()["error"]["code"]) == (503, "calendar_unavailable")
    row = await _row(tenant)
    assert row is not None and row.connection_status == "connected"


async def test_tenants_cannot_see_or_change_each_others_connection(
    client, entitled_tenants, calendar
):
    tenant_a, tenant_b = entitled_tenants
    await _connected(client, tenant_a, calendar)
    headers_b = _auth(tenant_b)

    status = await client.get("/api/v1/calendar/status", headers=headers_b)
    assert status.json()["status"] == "not_connected" and status.json()["calendar_id"] is None
    for response in (
        await client.get("/api/v1/calendar/calendars", headers=headers_b),
        await client.patch("/api/v1/calendar", json={"calendar_id": OWNED.id}, headers=headers_b),
        await client.delete("/api/v1/calendar", headers=headers_b),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "calendar_not_connected",
        )

    mine = (await client.get("/api/v1/calendar/status", headers=_auth(tenant_a))).json()
    assert (mine["status"], mine["calendar_id"]) == ("connected", OWNED.id)
    assert calendar.api.revoked == set()
    assert await _row(tenant_b) is None


async def test_disconnect_revokes_at_google_and_deletes_the_token(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    headers = _auth(tenant)
    refresh = await _connected(client, tenant, calendar)

    response = await client.delete("/api/v1/calendar", headers=headers)

    assert (response.status_code, response.json()) == (200, {"revoked": True})
    assert refresh in calendar.api.revoked
    assert await _row(tenant) is None
    assert (await client.get("/api/v1/calendar/status", headers=headers)).json()["status"] == (
        "not_connected"
    )
    again = await client.delete("/api/v1/calendar", headers=headers)
    assert (again.status_code, again.json()["error"]["code"]) == (404, "calendar_not_connected")


async def test_disconnect_still_deletes_when_google_cannot_confirm(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    await _connected(client, tenant, calendar)
    calendar.api.fail_next("revoke", TRANSIENT, times=3)

    response = await client.delete("/api/v1/calendar", headers=_auth(tenant))

    assert (response.status_code, response.json()) == (200, {"revoked": False})
    assert await _row(tenant) is None


async def test_validation_entitlement_and_configuration(client, entitled_tenants, two_tenants):
    # No `calendar` fixture here: the app is unconfigured (no Google credentials or keys).
    tenant, _ = entitled_tenants
    headers = _auth(tenant)

    status = await client.get("/api/v1/calendar/status", headers=headers)
    assert (status.status_code, status.json()["status"]) == (200, "not_connected")
    for response in (
        await client.get("/api/v1/calendar/connect", headers=headers),
        await client.post(
            "/api/v1/calendar/callback", json={"code": "c", "state": "s"}, headers=headers
        ),
        await client.get("/api/v1/calendar/calendars", headers=headers),
        await client.patch("/api/v1/calendar", json={"calendar_id": "x"}, headers=headers),
        await client.delete("/api/v1/calendar", headers=headers),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (
            503,
            "calendar_not_configured",
        )

    for body in ({}, {"calendar_id": ""}, {"calendar_id": "x" * 1025}):
        bad = await client.patch("/api/v1/calendar", json=body, headers=headers)
        assert bad.status_code == 422
    for body in ({"code": "", "state": "s"}, {"code": "c", "state": "s" * 4097}):
        bad = await client.post("/api/v1/calendar/callback", json=body, headers=headers)
        assert bad.status_code == 422


async def test_every_route_requires_an_active_plan_and_a_login(client, calendar, two_tenants):
    # `two_tenants` onboarded but never started a trial.
    tenant, _ = two_tenants
    routes = [
        ("GET", "/api/v1/calendar/connect"),
        ("POST", "/api/v1/calendar/callback"),
        ("GET", "/api/v1/calendar/status"),
        ("GET", "/api/v1/calendar/calendars"),
        ("PATCH", "/api/v1/calendar"),
        ("DELETE", "/api/v1/calendar"),
    ]
    for method, path in routes:
        paid = await client.request(
            method,
            path,
            headers=_auth(tenant),
            json={"code": "c", "state": "s", "calendar_id": "x"},
        )
        assert (paid.status_code, paid.json()["error"]["code"]) == (402, "subscription_required")
        anonymous = await client.request(method, path)
        assert anonymous.status_code == 401


async def _user_id(tenant):
    """The user id the API resolves for this tenant's bearer (needed to forge states)."""
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(User.id).where(User.external_auth_id == tenant.external_auth_id)
            )
        ).scalar_one()
