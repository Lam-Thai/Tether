"""The owner's inbox over real Postgres: pagination, filters, detail, transcript, the
idle sweep, lazy summaries, End -> summary, and tenant isolation."""

import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.db import async_session_factory
from integrations.llm.fake import agent_turn, summary_reply
from models.interaction import Interaction
from tests.api.inbox_factory import BASE, add_booking, add_interaction, utc


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _list(client, tenant, **params):
    response = await client.get("/api/v1/interactions", params=params, headers=_auth(tenant))
    assert response.status_code == 200, response.text
    return response.json()


async def _all_pages(client, tenant, **params) -> list[dict]:
    items: list[dict] = []
    cursor = None
    for _ in range(20):
        body = await _list(client, tenant, **params, **({"cursor": cursor} if cursor else {}))
        items += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("pagination did not end")


# --- access ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["", "/{id}", "/{id}/messages"], ids=["list", "detail", "messages"]
)
async def test_routes_need_a_token_and_a_plan(client, two_tenants, path):
    url = "/api/v1/interactions" + path.format(id=uuid.uuid4())
    assert (await client.get(url)).status_code == 401
    unpaid = await client.get(url, headers=_auth(two_tenants[0]))
    assert unpaid.status_code == 402


async def test_an_empty_inbox_returns_the_business_timezone(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    assert await _list(client, tenant_a) == {
        "items": [],
        "next_cursor": None,
        "timezone": "America/New_York",
    }


# --- pagination --------------------------------------------------------------------


async def test_pages_are_stable_newest_first_with_ties_and_no_gaps(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    created: list[tuple] = []
    for n in range(57):
        # Groups of 4 share the same started_at: the id breaks the tie.
        started_at = BASE - timedelta(minutes=n // 4)
        interaction_id = await add_interaction(tenant_a.business_id, started_at=started_at)
        created.append((started_at, interaction_id))

    first = await _list(client, tenant_a, limit=25)
    assert len(first["items"]) == 25 and first["next_cursor"]
    items = await _all_pages(client, tenant_a, limit=25)

    expected = [str(i) for _, i in sorted(created, key=lambda c: (c[0], c[1]), reverse=True)]
    assert [item["id"] for item in items] == expected
    assert len({item["id"] for item in items}) == 57


async def test_a_page_boundary_inside_a_tie_loses_and_repeats_nothing(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    ids = [await add_interaction(tenant_a.business_id, started_at=BASE) for _ in range(7)]

    seen = [item["id"] for item in await _all_pages(client, tenant_a, limit=3)]

    assert seen == [str(i) for i in sorted(ids, reverse=True)]


async def test_a_newer_interaction_between_pages_does_not_shift_the_next_page(
    client, entitled_tenants
):
    tenant_a, _ = entitled_tenants
    for n in range(6):
        await add_interaction(tenant_a.business_id, started_at=BASE - timedelta(hours=n))
    first = await _list(client, tenant_a, limit=3)

    newer = await add_interaction(tenant_a.business_id, started_at=BASE + timedelta(hours=1))
    second = await _list(client, tenant_a, limit=3, cursor=first["next_cursor"])

    assert str(newer) not in {i["id"] for i in second["items"]}
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert len(second["items"]) == 3 and second["next_cursor"] is None


async def test_the_last_full_page_has_no_next_cursor(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    for n in range(4):
        await add_interaction(tenant_a.business_id, started_at=BASE - timedelta(hours=n))

    body = await _list(client, tenant_a, limit=4)

    assert len(body["items"]) == 4 and body["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [{"cursor": "garbage!"}, {"cursor": "bm90LWEtY3Vyc29y"}, {"cursor": "x" * 300}],
    ids=["not-base64", "no-separator", "too-long"],
)
async def test_an_invalid_cursor_is_a_400(client, entitled_tenants, params):
    tenant_a, _ = entitled_tenants
    response = await client.get("/api/v1/interactions", params=params, headers=_auth(tenant_a))
    assert response.status_code in (400, 422)
    if "x" * 300 != params["cursor"]:
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_cursor"


async def test_from_after_to_is_a_422_and_bad_params_are_rejected(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    inverted = await client.get(
        "/api/v1/interactions",
        params={"from": "2026-10-21", "to": "2026-10-20"},
        headers=_auth(tenant_a),
    )
    assert inverted.status_code == 422
    assert inverted.json()["error"]["code"] == "invalid_date_range"
    for params in (
        {"limit": 0},
        {"limit": 101},
        {"type": "nope"},
        {"status": "ended"},
        {"intent": "NOPE"},
        {"from": "yesterday"},
    ):
        response = await client.get("/api/v1/interactions", params=params, headers=_auth(tenant_a))
        assert response.status_code == 422, params


# --- filters -----------------------------------------------------------------------


@pytest.fixture
async def dataset(entitled_tenants):
    """One interaction per interesting shape, plus the other tenant's lookalikes."""
    tenant_a, tenant_b = entitled_tenants
    a = tenant_a.business_id
    rows = {
        "booked": dict(
            started_at=utc(2026, 10, 18, 14),
            intent="BOOK_APPOINTMENT",
            status="completed",
            outcome="booked",
        ),
        "booking_failed": dict(
            started_at=utc(2026, 10, 19, 14),
            intent="BOOK_APPOINTMENT",
            status="failed",
            outcome="failed",
            state={"requires_human": True},
        ),
        "cancel": dict(
            started_at=utc(2026, 10, 20, 14),
            intent="CANCEL",
            status="completed",
            outcome="escalated",
            state={"requires_human": True},
        ),
        "faq": dict(started_at=utc(2026, 10, 20, 15), intent="FAQ"),
        "hours": dict(
            started_at=utc(2026, 10, 21, 3, 30), intent="BUSINESS_HOURS"
        ),  # 23:30 EDT 20th
        "price": dict(started_at=utc(2026, 10, 21, 4, 0), intent="PRICE_INQUIRY"),  # 00:00 EDT 21st
        "human": dict(
            started_at=utc(2026, 10, 22, 14),
            intent="HUMAN_REQUEST",
            outcome="escalated",
        ),
        "abandoned": dict(
            started_at=utc(2026, 10, 23, 14),
            intent="LOCATION",
            status="abandoned",
            outcome="abandoned",
        ),
        "unknown": dict(started_at=utc(2026, 10, 24, 14), intent=None, outcome="answered"),
        "flagged_open": dict(
            started_at=utc(2026, 10, 25, 14),
            intent="URGENT",
            status="in_progress",
            outcome=None,
            state={"requires_human": True},
            updated_at=utc(2200, 1, 1),  # never idle
        ),
    }
    ids = {}
    for name, fields in rows.items():
        ids[name] = str(await add_interaction(a, **fields))
    # The other business has rows that would match every filter.
    for fields in rows.values():
        await add_interaction(tenant_b.business_id, **fields)
    return ids


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {},
            {
                "booked",
                "booking_failed",
                "cancel",
                "faq",
                "hours",
                "price",
                "human",
                "abandoned",
                "unknown",
                "flagged_open",
            },
        ),
        (
            {"type": "all"},
            {
                "booked",
                "booking_failed",
                "cancel",
                "faq",
                "hours",
                "price",
                "human",
                "abandoned",
                "unknown",
                "flagged_open",
            },
        ),
        ({"type": "bookings"}, {"booked", "booking_failed", "cancel"}),
        ({"type": "faqs"}, {"faq", "hours", "price", "abandoned"}),
        ({"type": "escalations"}, {"booking_failed", "cancel", "human", "flagged_open"}),
        ({"type": "failed"}, {"booking_failed"}),
        (
            {"status": "completed"},
            {"booked", "cancel", "faq", "hours", "price", "human", "unknown"},
        ),
        ({"status": "in_progress"}, {"flagged_open"}),
        ({"status": "abandoned"}, {"abandoned"}),
        ({"intent": "BOOK_APPOINTMENT"}, {"booked", "booking_failed"}),
        ({"intent": "FAQ"}, {"faq"}),
        ({"from": "2026-10-20", "to": "2026-10-20"}, {"cancel", "faq", "hours"}),
        ({"from": "2026-10-21", "to": "2026-10-21"}, {"price"}),
        ({"from": "2026-10-24"}, {"unknown", "flagged_open"}),
        ({"to": "2026-10-19"}, {"booked", "booking_failed"}),
        ({"type": "bookings", "status": "failed"}, {"booking_failed"}),
        ({"type": "bookings", "intent": "CANCEL"}, {"cancel"}),
        ({"type": "escalations", "status": "completed"}, {"cancel", "human"}),
        ({"type": "faqs", "status": "abandoned"}, {"abandoned"}),
        (
            {"type": "escalations", "from": "2026-10-22", "to": "2026-10-25"},
            {"human", "flagged_open"},
        ),
        (
            {
                "type": "bookings",
                "status": "completed",
                "intent": "BOOK_APPOINTMENT",
                "from": "2026-10-18",
                "to": "2026-10-18",
            },
            {"booked"},
        ),
        ({"type": "failed", "status": "completed"}, set()),
        ({"intent": "FAQ", "from": "2026-10-21"}, set()),
    ],
    ids=[
        "default",
        "all",
        "bookings",
        "faqs",
        "escalations",
        "failed",
        "status-completed",
        "status-in-progress",
        "status-abandoned",
        "intent-book",
        "intent-faq",
        "day-in-business-tz",
        "day-starts-at-local-midnight",
        "from-only",
        "to-only",
        "bookings-and-failed",
        "bookings-and-intent",
        "escalations-and-completed",
        "faqs-and-abandoned",
        "escalations-and-dates",
        "four-filters",
        "contradiction",
        "no-match-with-date",
    ],
)
async def test_filters_and_their_combinations(client, entitled_tenants, dataset, params, expected):
    tenant_a, _ = entitled_tenants

    items = await _all_pages(client, tenant_a, **params)

    by_id = {v: k for k, v in dataset.items()}
    assert {by_id[item["id"]] for item in items} == expected
    assert len(items) == len(expected)


async def test_filtered_results_page_too(client, entitled_tenants, dataset):
    tenant_a, _ = entitled_tenants
    first = await _list(client, tenant_a, type="bookings", limit=2)
    assert len(first["items"]) == 2 and first["next_cursor"]
    rest = await _list(client, tenant_a, type="bookings", limit=2, cursor=first["next_cursor"])
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None


async def test_row_fields(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    await add_interaction(
        tenant_a.business_id,
        intent="BOOK_APPOINTMENT",
        outcome="booked",
        summary="Wanted a cut. A cut was booked.",
        summary_source="ai",
        state={"entities": {"customer_name": "Alex"}, "requires_human": False},
    )
    await add_interaction(
        tenant_a.business_id,
        started_at=BASE - timedelta(hours=1),
        caller="+15555550123",
        channel="voice",
        state={"entities": {"customer_name": "Sam"}},
    )

    first, second = (await _list(client, tenant_a))["items"]

    assert first["caller"] == "Alex" and second["caller"] == "+15555550123"  # number wins
    assert first["summary"] == "Wanted a cut. A cut was booked."
    assert (first["summary_source"], first["outcome"], first["status"]) == (
        "ai",
        "booked",
        "completed",
    )
    assert first["intent"] == "BOOK_APPOINTMENT" and first["requires_human"] is False
    assert first["channel"] == "test_chat" and second["channel"] == "voice"
    assert first["started_at"].endswith("Z") or "+00:00" in first["started_at"]
    assert second["summary"] is None and second["summary_source"] is None


# --- tenant isolation ----------------------------------------------------------------


async def test_another_businesss_interaction_is_never_listed_and_is_a_404(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    foreign = await add_interaction(
        tenant_b.business_id,
        state={"requires_human": True},
        operations=(("search_faq", "succeeded", {"count": 1}),),
    )
    mine = await add_interaction(tenant_a.business_id)

    listed = await _all_pages(client, tenant_a)
    detail = await client.get(f"/api/v1/interactions/{foreign}", headers=_auth(tenant_a))
    messages = await client.get(f"/api/v1/interactions/{foreign}/messages", headers=_auth(tenant_a))
    unknown = await client.get(f"/api/v1/interactions/{uuid.uuid4()}", headers=_auth(tenant_a))

    assert [i["id"] for i in listed] == [str(mine)]
    assert detail.status_code == messages.status_code == unknown.status_code == 404
    assert detail.json()["error"]["code"] == "interaction_not_found"
    # ...and it is still there for its owner.
    assert (
        await client.get(f"/api/v1/interactions/{foreign}", headers=_auth(tenant_b))
    ).status_code == 200


async def test_a_cursor_from_another_business_leaks_nothing(client, entitled_tenants):
    tenant_a, tenant_b = entitled_tenants
    for n in range(3):
        await add_interaction(tenant_b.business_id, started_at=BASE - timedelta(hours=n))
        await add_interaction(tenant_a.business_id, started_at=BASE - timedelta(hours=n))
    theirs = await _list(client, tenant_b, limit=1)

    mine = await _list(client, tenant_a, limit=5, cursor=theirs["next_cursor"])

    assert all(i["id"] not in {j["id"] for j in theirs["items"]} for i in mine["items"])
    ids_a = {i["id"] for i in (await _list(client, tenant_a))["items"]}
    assert {i["id"] for i in mine["items"]} <= ids_a


# --- detail and transcript --------------------------------------------------------------


async def test_detail_has_the_operation_timeline_with_machine_labels(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    booking_id = str(uuid.uuid4())
    interaction_id = await add_interaction(
        tenant_a.business_id,
        intent="BOOK_APPOINTMENT",
        outcome="booked",
        summary="Wanted a cut. A cut was booked.",
        summary_source="template",
        messages=(
            ("agent", "Hi!"),
            ("customer", "Book me"),
            ("agent", "Done"),
            ("customer", "Thx"),
        ),
        operations=(
            ("check_availability", "succeeded", {"result": "available", "offered": 3}),
            ("create_booking", "succeeded", {"result": "confirmed", "booking_id": booking_id}),
            ("create_booking", "rejected", {"reason": "invalid_arguments", "fields": ["x"]}),
        ),
    )

    response = await client.get(f"/api/v1/interactions/{interaction_id}", headers=_auth(tenant_a))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timezone"] == "America/New_York" and body["message_count"] == 4
    assert [(o["tool_name"], o["status"], o["label"]) for o in body["operations"]] == [
        ("check_availability", "succeeded", "3 slots"),
        ("create_booking", "succeeded", f"booking {booking_id[:8]}"),
        ("create_booking", "rejected", "invalid_arguments"),
    ]
    assert set(body["operations"][0]) == {
        "id",
        "tool_name",
        "status",
        "label",
        "created_at",
        "duration_ms",
    }
    assert body["summary"] == "Wanted a cut. A cut was booked."


async def test_messages_page_by_sequence_and_are_redacted_on_read(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    texts = [("agent" if n % 2 else "customer", f"message {n}") for n in range(1, 121)]
    texts[1] = ("customer", "my card is 4111 1111 1111 1111 and cvv 123, ssn 123-45-6789")
    interaction_id = await add_interaction(
        tenant_a.business_id, messages=tuple(texts), state={"fallback_messages": [3, 4]}
    )
    url = f"/api/v1/interactions/{interaction_id}/messages"

    first = (await client.get(url, params={"limit": 50}, headers=_auth(tenant_a))).json()
    second = (
        await client.get(
            url, params={"after": first["next_after"], "limit": 50}, headers=_auth(tenant_a)
        )
    ).json()
    last = (
        await client.get(
            url, params={"after": second["next_after"], "limit": 50}, headers=_auth(tenant_a)
        )
    ).json()

    sequence = [m["sequence_number"] for m in first["items"] + second["items"] + last["items"]]
    assert sequence == list(range(1, 121))
    assert (first["next_after"], second["next_after"], last["next_after"]) == (50, 100, None)
    stored_secret = first["items"][1]["text"]
    assert "4111" not in stored_secret and "123" not in stored_secret
    # agent message 3 is flagged; customer message 4 cannot be a fallback
    assert [m["fallback"] for m in first["items"][2:4]] == [True, False]
    assert (
        await client.get(url, params={"limit": 201}, headers=_auth(tenant_a))
    ).status_code == 422
    assert (await client.get(url, params={"after": -1}, headers=_auth(tenant_a))).status_code == 422


async def test_stored_secrets_are_redacted_in_the_summary_too(client, entitled_tenants):
    tenant_a, _ = entitled_tenants
    interaction_id = await add_interaction(
        tenant_a.business_id,
        summary="The customer paid with 4111111111111111. It was paid.",
        summary_source="template",
    )

    body = (
        await client.get(f"/api/v1/interactions/{interaction_id}", headers=_auth(tenant_a))
    ).json()

    assert "4111" not in body["summary"]


# --- lazy summary --------------------------------------------------------------------------


async def test_detail_summarizes_a_closed_interaction_with_no_summary_once(
    client, entitled_tenants, llm
):
    tenant_a, _ = entitled_tenants
    interaction_id = await add_interaction(
        tenant_a.business_id,
        messages=(("agent", "Hi!"), ("customer", "Do you take walk-ins?"), ("agent", "Yes.")),
    )
    llm.queue(
        summary_reply("The customer asked about walk-ins. They were told walk-ins are welcome.")
    )
    url = f"/api/v1/interactions/{interaction_id}"

    first = (await client.get(url, headers=_auth(tenant_a))).json()
    second = (await client.get(url, headers=_auth(tenant_a))).json()

    assert first["summary"] and first["summary_source"] in ("ai", "template")
    assert second["summary"] == first["summary"]
    assert len(llm.requests) <= 2  # at most one call and its one retry; none on the second read


async def test_detail_with_an_unavailable_model_stores_the_template(client, entitled_tenants, llm):
    from integrations.llm.port import LLMUnavailableError

    tenant_a, _ = entitled_tenants
    interaction_id = await add_interaction(tenant_a.business_id)
    llm.queue(*[LLMUnavailableError("down", retryable=False)] * 3)

    body = (
        await client.get(f"/api/v1/interactions/{interaction_id}", headers=_auth(tenant_a))
    ).json()

    assert body["summary"] and body["summary_source"] == "template"


async def test_detail_of_an_open_interaction_makes_no_model_call(client, entitled_tenants, llm):
    tenant_a, _ = entitled_tenants
    interaction_id = await add_interaction(
        tenant_a.business_id,
        status="in_progress",
        outcome=None,
        updated_at=utc(2200, 1, 1),  # far in the future: never idle
    )

    body = (
        await client.get(f"/api/v1/interactions/{interaction_id}", headers=_auth(tenant_a))
    ).json()

    assert body["summary"] is None and body["status"] == "in_progress"
    assert llm.requests == []


# --- the idle sweep -------------------------------------------------------------------------


async def test_listing_abandons_idle_conversations_without_any_model_call(
    client, entitled_tenants, llm
):
    tenant_a, tenant_b = entitled_tenants
    long_ago = BASE - timedelta(days=3)
    idle = await add_interaction(
        tenant_a.business_id,
        status="in_progress",
        outcome=None,
        started_at=long_ago,
        updated_at=long_ago + timedelta(minutes=2),
        state={"requires_human": True},
    )
    idle_plain = await add_interaction(
        tenant_a.business_id,
        status="in_progress",
        outcome=None,
        started_at=long_ago - timedelta(hours=1),
        updated_at=long_ago,
    )
    fresh = await add_interaction(
        tenant_a.business_id,
        status="in_progress",
        outcome=None,
        updated_at=utc(2200, 1, 1),
    )
    foreign_idle = await add_interaction(
        tenant_b.business_id,
        status="in_progress",
        outcome=None,
        started_at=long_ago,
        updated_at=long_ago,
    )

    items = {i["id"]: i for i in (await _list(client, tenant_a))["items"]}

    assert (items[str(idle)]["status"], items[str(idle)]["outcome"]) == ("abandoned", "escalated")
    assert (items[str(idle_plain)]["status"], items[str(idle_plain)]["outcome"]) == (
        "abandoned",
        "abandoned",
    )
    assert items[str(idle)]["ended_at"].startswith("2026-10-17T15:02")  # ended_at = last activity
    assert items[str(fresh)]["status"] == "in_progress" and items[str(fresh)]["outcome"] is None
    assert llm.requests == []
    assert items[str(idle)]["summary"] and items[str(idle)]["summary_source"] == "template"
    async with async_session_factory() as session:
        row = (
            await session.execute(select(Interaction).where(Interaction.id == foreign_idle))
        ).scalar_one()
    assert row.status == "in_progress"  # another business's sweep is not ours to run
    # The close is durable: a second read sees the same thing.
    again = {i["id"]: i for i in (await _list(client, tenant_a, status="abandoned"))["items"]}
    assert set(again) == {str(idle), str(idle_plain)}


# --- End -> summary, end to end --------------------------------------------------------------


async def _chat(client, tenant, message, interaction_id=None):
    return await client.post(
        "/api/v1/agent/test",
        json={"interaction_id": interaction_id, "message": message},
        headers=_auth(tenant),
    )


async def test_ending_a_chat_closes_it_then_writes_the_summary_and_the_inbox_shows_it(
    client, entitled_tenants, llm
):
    tenant_a, _ = entitled_tenants
    llm.queue(
        agent_turn("Happy to help with that.", intent="HUMAN_REQUEST"),
        summary_reply("The customer asked about walk-ins. They were told walk-ins are welcome."),
    )
    turn = (await _chat(client, tenant_a, "Do you take walk-ins? My card 4111111111111111")).json()
    interaction_id = turn["interaction_id"]

    ended = await client.post(f"/api/v1/agent/test/{interaction_id}/end", headers=_auth(tenant_a))

    assert ended.status_code == 200, ended.text
    assert (ended.json()["status"], ended.json()["ended"]) == ("completed", True)
    detail = (
        await client.get(f"/api/v1/interactions/{interaction_id}", headers=_auth(tenant_a))
    ).json()
    assert (detail["status"], detail["outcome"], detail["intent"]) == (
        "completed",
        "answered",
        "HUMAN_REQUEST",
    )
    assert detail["summary"] and detail["summary_source"] in ("ai", "template")
    assert detail["ended_at"] is not None
    # The customer's card never reached the model.
    assert all("4111" not in m.text for r in llm.requests for m in r.messages)
    transcript = (
        await client.get(f"/api/v1/interactions/{interaction_id}/messages", headers=_auth(tenant_a))
    ).json()
    assert all("4111" not in m["text"] for m in transcript["items"])
    # Ending again is harmless and the summary stays.
    again = await client.post(f"/api/v1/agent/test/{interaction_id}/end", headers=_auth(tenant_a))
    assert again.status_code == 200 and again.json()["ended_at"] == ended.json()["ended_at"]
    after = (
        await client.get(f"/api/v1/interactions/{interaction_id}", headers=_auth(tenant_a))
    ).json()
    assert after["summary"] == detail["summary"]


async def test_a_summarizer_that_cannot_run_does_not_fail_the_end(client, entitled_tenants, llm):
    tenant_a, _ = entitled_tenants
    llm.queue(agent_turn("Hello!"))  # nothing scripted for the summary: the fake raises
    interaction_id = (await _chat(client, tenant_a, "hi")).json()["interaction_id"]

    ended = await client.post(f"/api/v1/agent/test/{interaction_id}/end", headers=_auth(tenant_a))

    assert ended.status_code == 200 and ended.json()["status"] == "completed"
    detail = (
        await client.get(f"/api/v1/interactions/{interaction_id}", headers=_auth(tenant_a))
    ).json()
    assert detail["status"] == "completed"


def _abort_the_summary_transaction(monkeypatch) -> None:
    """Make the summarizer's claim fail with a real database error, which leaves the
    request session's transaction aborted until someone rolls it back."""
    from sqlalchemy import text

    from repositories.summaries import SqlSummaryStore

    async def broken_claim(self, *args, **kwargs):
        await self._session.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(SqlSummaryStore, "claim", broken_claim)


async def test_a_database_error_in_the_summary_does_not_fail_the_end(
    client, entitled_tenants, llm, monkeypatch
):
    tenant_a, _ = entitled_tenants
    llm.queue(agent_turn("Hello!"))
    interaction_id = (await _chat(client, tenant_a, "hi")).json()["interaction_id"]
    _abort_the_summary_transaction(monkeypatch)

    ended = await client.post(f"/api/v1/agent/test/{interaction_id}/end", headers=_auth(tenant_a))

    assert ended.status_code == 200 and ended.json()["status"] == "completed"


async def test_a_database_error_in_the_lazy_summary_does_not_fail_the_detail(
    client, entitled_tenants, monkeypatch
):
    tenant_a, _ = entitled_tenants
    interaction_id = await add_interaction(tenant_a.business_id)
    _abort_the_summary_transaction(monkeypatch)

    response = await client.get(f"/api/v1/interactions/{interaction_id}", headers=_auth(tenant_a))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(interaction_id) and body["summary"] is None
    assert body["timezone"]


async def test_out_of_range_inputs_are_client_errors_not_500s(client, entitled_tenants):
    import base64

    tenant_a, _ = entitled_tenants
    overflow = base64.urlsafe_b64encode(
        f"0001-01-01T00:00:00+05:00|{uuid.uuid4()}".encode()
    ).decode()

    cursor = await client.get(
        "/api/v1/interactions", params={"cursor": overflow}, headers=_auth(tenant_a)
    )
    far = await client.get(
        "/api/v1/interactions", params={"to": "9999-12-31"}, headers=_auth(tenant_a)
    )

    assert cursor.status_code == 400 and cursor.json()["error"]["code"] == "invalid_cursor"
    assert far.status_code == 422


async def test_a_booking_confirmed_after_the_conversation_closed_counts_as_booked(
    client, entitled_tenants
):
    tenant_a, tenant_b = entitled_tenants
    late = await add_interaction(tenant_a.business_id, intent="FAQ", outcome="answered")
    await add_booking(tenant_a.business_id, late)
    pending_only = await add_interaction(
        tenant_a.business_id, intent="FAQ", outcome="answered", started_at=BASE - timedelta(hours=1)
    )
    await add_booking(tenant_a.business_id, pending_only, status="pending", day=40)
    plain = await add_interaction(
        tenant_a.business_id, intent="FAQ", outcome="answered", started_at=BASE - timedelta(hours=2)
    )

    bookings = {i["id"]: i for i in (await _list(client, tenant_a, type="bookings"))["items"]}
    everything = {i["id"]: i for i in (await _list(client, tenant_a))["items"]}
    detail = (await client.get(f"/api/v1/interactions/{late}", headers=_auth(tenant_a))).json()

    assert set(bookings) == {str(late)}
    assert bookings[str(late)]["outcome"] == "booked" and detail["outcome"] == "booked"
    assert everything[str(pending_only)]["outcome"] == "answered"
    assert everything[str(plain)]["outcome"] == "answered"
    # Another business's confirmed booking on a lookalike never leaks across.
    assert (await _list(client, tenant_b, type="bookings"))["items"] == []


async def test_a_failing_idle_sweep_does_not_break_the_list(client, entitled_tenants, monkeypatch):
    from repositories import interactions as repo

    tenant_a, _ = entitled_tenants
    mine = await add_interaction(tenant_a.business_id)

    async def broken(*args, **kwargs):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(repo, "lock_idle", broken)

    body = await _list(client, tenant_a)

    assert [i["id"] for i in body["items"]] == [str(mine)]
