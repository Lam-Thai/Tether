"""`calendar_connections` repository against real Postgres: upsert, scoping, cascade."""

from datetime import UTC, datetime

from sqlalchemy import delete, func, select

from core.db import async_session_factory
from models.business import Business
from models.calendar import CalendarConnection
from repositories import calendar as repo

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


async def _store(session, business_id, token: bytes = b"ciphertext-a"):
    return await repo.upsert_tokens(
        session,
        business_id,
        encrypted_refresh_token=token,
        token_key_id="k1",
        granted_scopes="s1 s2",
        synced_at=NOW,
    )


async def test_upsert_replaces_the_token_and_clears_the_selection(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        first = await _store(session, tenant_a.business_id)
        await repo.set_selection(
            session,
            tenant_a.business_id,
            calendar_id="cal",
            calendar_name="Work",
            synced_at=NOW,
            seen_updated_at=first.updated_at,
        )
        second = await _store(session, tenant_a.business_id, token=b"ciphertext-b")
        await session.commit()

    async with async_session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(CalendarConnection)
            .where(CalendarConnection.business_id == tenant_a.business_id)
        )
        row = await repo.get_connection(session, tenant_a.business_id)

    assert count == 1 and first.id == second.id
    assert row is not None
    assert bytes(row.encrypted_refresh_token) == b"ciphertext-b"
    assert (row.connection_status, row.calendar_id, row.calendar_name) == (
        "needs_selection",
        None,
        None,
    )


async def test_every_query_is_scoped_to_the_business(two_tenants):
    tenant_a, tenant_b = two_tenants
    async with async_session_factory() as session:
        await _store(session, tenant_a.business_id)
        await session.commit()

    async with async_session_factory() as session:
        b = tenant_b.business_id
        assert await repo.get_connection(session, b) is None
        assert (
            await repo.set_selection(
                session, b, calendar_id="x", calendar_name="x", synced_at=NOW, seen_updated_at=NOW
            )
            is None
        )
        assert (
            await repo.mark_status(
                session,
                b,
                status="reconnect_needed",
                last_error="auth_revoked",
                last_synced_at=NOW,
                seen_updated_at=NOW,
            )
            is None
        )
        assert await repo.delete_connection(session, b) is None
        await session.commit()

    async with async_session_factory() as session:
        row = await repo.get_connection(session, tenant_a.business_id)
    assert row is not None and row.connection_status == "needs_selection"


async def test_mark_status_and_delete(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    async with async_session_factory() as session:
        stored = await _store(session, a)
        selected = await repo.set_selection(
            session,
            a,
            calendar_id="c",
            calendar_name="C",
            synced_at=NOW,
            seen_updated_at=stored.updated_at,
        )
        assert selected is not None
        missing = await repo.mark_status(
            session,
            a,
            status="needs_selection",
            last_error="calendar_missing",
            last_synced_at=NOW,
            seen_updated_at=selected.updated_at,
            clear_selection=True,
        )
        assert missing is not None
        assert (missing.calendar_id, missing.last_error) == (None, "calendar_missing")
        deleted = await repo.delete_connection(session, a)
        await session.commit()
        assert deleted is not None and await repo.get_connection(session, a) is None


async def test_deleting_the_business_cascades(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        await _store(session, tenant_a.business_id)
        await session.commit()
        # `two_tenants` cleanup deletes the business again; doing it here proves the cascade.
        await session.execute(delete(Business).where(Business.id == tenant_a.business_id))
        await session.commit()
        assert await repo.get_connection(session, tenant_a.business_id) is None


async def test_a_write_based_on_a_stale_read_changes_nothing(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    async with async_session_factory() as session:
        seen = await _store(session, a, token=b"old")
        await session.commit()
    # Another tab reconnects: new token, new updated_at.
    async with async_session_factory() as session:
        reconnected = await _store(session, a, token=b"new")
        await session.commit()
    assert reconnected.updated_at != seen.updated_at

    async with async_session_factory() as session:
        results = [
            await repo.set_selection(
                session,
                a,
                calendar_id="c",
                calendar_name="C",
                synced_at=NOW,
                seen_updated_at=seen.updated_at,
            ),
            await repo.mark_status(
                session,
                a,
                status="reconnect_needed",
                last_error="auth_revoked",
                last_synced_at=NOW,
                seen_updated_at=seen.updated_at,
            ),
            await repo.reseal(
                session,
                a,
                encrypted_refresh_token=b"resealed-from-old",
                token_key_id="k2",
                seen_updated_at=seen.updated_at,
            ),
        ]
        await session.commit()

    for current in results:  # each call hands back the row as it really is
        assert current is not None
        assert bytes(current.encrypted_refresh_token) == b"new"
        assert (current.connection_status, current.calendar_id) == ("needs_selection", None)
    async with async_session_factory() as session:
        row = await repo.get_connection(session, a)
        assert row is not None and row.token_key_id == "k1"
        await repo.delete_connection(session, a)
        gone = await repo.reseal(
            session,
            a,
            encrypted_refresh_token=b"x",
            token_key_id="k2",
            seen_updated_at=row.updated_at,
        )
        assert gone is None


async def test_every_write_changes_updated_at_and_reseal_applies(two_tenants):
    tenant_a, _ = two_tenants
    a = tenant_a.business_id
    async with async_session_factory() as session:
        stored = await _store(session, a)
        first_stamp = stored.updated_at  # the ORM returns one identity-mapped object
        resealed = await repo.reseal(
            session,
            a,
            encrypted_refresh_token=b"under-new-key",
            token_key_id="k2",
            seen_updated_at=stored.updated_at,
        )
        assert resealed is not None
        second_stamp = resealed.updated_at
        marked = await repo.mark_status(
            session,
            a,
            status="connected",
            last_error=None,
            last_synced_at=NOW,
            seen_updated_at=resealed.updated_at,
        )
        assert marked is not None
        await session.commit()  # all three in ONE transaction, so now() would not differ

    stamps = [first_stamp, second_stamp, marked.updated_at]
    assert len(set(stamps)) == 3
    assert (bytes(marked.encrypted_refresh_token), marked.token_key_id) == (b"under-new-key", "k2")
