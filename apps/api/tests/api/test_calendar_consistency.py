"""Calendar connection edge cases: shared Google grants, concurrent changes, key rotation."""

import os
from dataclasses import replace

from core.db import async_session_factory
from integrations.google_calendar.port import REQUIRED_SCOPES, GoogleApiError
from repositories import calendar as repo
from services.calendar import token_context
from services.token_vault import SealedToken, TokenVault
from tests.api.test_calendar import (
    OWNED,
    SECOND,
    _auth,
    _connect,
    _connected,
    _row,
    client,  # noqa: F401  (fixture)
)

ACCOUNT = "one-google-account"


async def test_a_rejected_reconnect_leaves_the_working_connection_alone(
    client,  # noqa: F811
    entitled_tenants,
    calendar,
):
    tenant, _ = entitled_tenants
    headers = _auth(tenant)
    await _connected(client, tenant, calendar, account=ACCOUNT)
    before = await _row(tenant)

    partial = REQUIRED_SCOPES - {next(iter(REQUIRED_SCOPES))}
    missing, _ = await _connect(client, tenant, calendar, scopes=partial, account=ACCOUNT)
    nothing, _ = await _connect(client, tenant, calendar, refresh_token=None, account=ACCOUNT)

    assert missing.json()["error"]["code"] == "calendar_scopes_missing"
    assert nothing.json()["error"]["code"] == "calendar_grant_incomplete"
    assert calendar.api.revoked == set()
    after = await _row(tenant)
    assert before is not None and after is not None
    assert after.encrypted_refresh_token == before.encrypted_refresh_token
    listing = await client.get("/api/v1/calendar/calendars", headers=headers)
    assert listing.status_code == 200, listing.text
    status = (await client.get("/api/v1/calendar/status", headers=headers)).json()
    assert status["status"] == "connected"


async def test_only_invalid_grant_is_a_bad_state_other_auth_failures_are_unavailable(
    client,  # noqa: F811
    entitled_tenants,
    calendar,
):
    tenant, _ = entitled_tenants
    calendar.api.fail_next("exchange_code", GoogleApiError("invalid_client"))

    response, _ = await _connect(client, tenant, calendar)

    assert (response.status_code, response.json()["error"]["code"]) == (503, "calendar_unavailable")
    assert await _row(tenant) is None


async def test_a_successful_probe_reseals_the_token_under_the_active_key(
    client,  # noqa: F811
    entitled_tenants,
    calendar,
):
    tenant, _ = entitled_tenants
    headers = _auth(tenant)
    refresh = await _connected(client, tenant, calendar)
    old_row = await _row(tenant)
    assert old_row is not None and old_row.token_key_id == "test"

    # Rotate: a new key first, the old one kept for decrypting.
    calendar.vault = TokenVault({"new": os.urandom(32), "test": calendar.key}, "new")
    calendar.config = replace(calendar.config, status_check_seconds=0)

    status = (await client.get("/api/v1/calendar/status", headers=headers)).json()

    assert status["status"] == "connected"
    row = await _row(tenant)
    assert row is not None and row.token_key_id == "new"
    assert row.encrypted_refresh_token != old_row.encrypted_refresh_token
    sealed = SealedToken(row.token_key_id, bytes(row.encrypted_refresh_token))
    assert calendar.vault.decrypt(sealed, context=token_context(tenant.business_id)) == refresh
    # Still usable afterwards, and a second probe leaves it alone.
    assert (await client.get("/api/v1/calendar/calendars", headers=headers)).status_code == 200
    again = await _row(tenant)
    assert again is not None and again.encrypted_refresh_token == row.encrypted_refresh_token


async def test_select_loses_to_a_disconnect_or_reconnect_that_happened_meanwhile(
    client,  # noqa: F811
    entitled_tenants,
    calendar,
):
    tenant, _ = entitled_tenants
    headers = _auth(tenant)
    await _connect(client, tenant, calendar, calendars=[OWNED, SECOND])
    original = calendar.api.list_calendars

    async def during_google_call(change):
        async def wrapper(access_token):
            result = await original(access_token)
            await change()
            return result

        calendar.api.list_calendars = wrapper  # type: ignore[method-assign]

    async def disconnect():
        async with async_session_factory() as session:
            await repo.delete_connection(session, tenant.business_id)
            await session.commit()

    await during_google_call(disconnect)
    gone = await client.patch("/api/v1/calendar", json={"calendar_id": OWNED.id}, headers=headers)
    assert (gone.status_code, gone.json()["error"]["code"]) == (404, "calendar_not_connected")
    assert await _row(tenant) is None

    calendar.api.list_calendars = original  # type: ignore[method-assign]
    await _connect(client, tenant, calendar, calendars=[OWNED, SECOND])

    async def reconnect():
        async with async_session_factory() as session:
            await repo.upsert_tokens(
                session,
                tenant.business_id,
                encrypted_refresh_token=b"someone-elses-new-token",
                token_key_id="test",
                granted_scopes="x",
                synced_at=(await _row(tenant)).last_synced_at,  # type: ignore[union-attr]
            )
            await session.commit()

    await during_google_call(reconnect)
    stale = await client.patch("/api/v1/calendar", json={"calendar_id": OWNED.id}, headers=headers)
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "calendar_reconnect_needed")
    row = await _row(tenant)
    assert row is not None
    assert (row.connection_status, row.calendar_id) == ("needs_selection", None)
    assert bytes(row.encrypted_refresh_token) == b"someone-elses-new-token"
