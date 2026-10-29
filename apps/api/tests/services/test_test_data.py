"""`TestDataEraser`: the confirmation phrase, and the best-effort calendar cancellation rules,
on an in-memory store and a scripted `cancel_event`. The Postgres deletes are
`tests/repositories/test_test_data.py`; the whole flow over the API with the fake Google
Calendar is `tests/api/test_test_data.py`."""

import uuid
from collections.abc import Callable
from datetime import date

import pytest

from core.tenant import Tenant
from models.business import Business
from models.user import User
from services.business import BusinessNotFoundError
from services.calendar import (
    CalendarAuthRevokedError,
    CalendarNotFoundError,
    CalendarNotSelectedError,
    CalendarUnavailableError,
)
from services.test_data import (
    CONFIRMATION_PHRASE,
    ConfirmationMismatchError,
    ErasedCounts,
    TestDataEraser,
)

TODAY = date(2031, 6, 1)


class MemoryStore:
    __test__ = False

    def __init__(self, event_ids: list[str]) -> None:
        self.event_ids = event_ids
        # Events of bookings that appear after the snapshot but before the delete.
        self.late_event_ids: list[str] = []
        self.asked: list[uuid.UUID] = []
        self.erased: list[tuple[uuid.UUID, date]] = []

    async def calendar_event_ids(self, business_id: uuid.UUID) -> list[str]:
        self.asked.append(business_id)
        return list(self.event_ids)

    async def erase(self, business_id: uuid.UUID, *, keep_usage_from: date) -> ErasedCounts:
        self.erased.append((business_id, keep_usage_from))
        return ErasedCounts(
            interactions=3,
            messages=9,
            bookings=2,
            escalations=1,
            usage_events=7,
            calendar_event_ids=tuple([*self.event_ids, *self.late_event_ids]),
        )


class Clock:
    """The eraser's injected monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Calendar:
    """A scripted `cancel_event`: `outcomes[event_id]` is an exception to raise (default:
    success). Records every call."""

    def __init__(
        self,
        outcomes: dict[str, Exception] | None = None,
        clock: "Clock | None" = None,
        takes: float = 0.0,
        on_call: Callable[[str], None] | None = None,
    ) -> None:
        self.outcomes = outcomes or {}
        self.clock = clock
        self.takes = takes
        self.on_call = on_call
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def __call__(self, business_id: uuid.UUID, event_id: str) -> None:
        self.calls.append((business_id, event_id))
        if self.clock is not None:
            self.clock.now += self.takes  # the call "takes" this long
        if self.on_call is not None:
            self.on_call(event_id)
        if event_id in self.outcomes:
            raise self.outcomes[event_id]


def _tenant() -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone="UTC",
    )
    return Tenant(user=user, business=business)


def _eraser(store: MemoryStore, calendar: Calendar, **kwargs) -> TestDataEraser:
    return TestDataEraser(store, calendar, lambda: TODAY, **kwargs)


@pytest.mark.parametrize(
    "typed",
    [
        pytest.param("", id="empty"),
        pytest.param("delete test data", id="lower-case"),
        pytest.param("DELETE", id="partial"),
        pytest.param("DELETE TEST DATA NOW", id="extra-words"),
        pytest.param("DELETE  TEST DATA", id="inner-double-space"),
        pytest.param("yes", id="yes"),
    ],
)
async def test_a_mismatch_touches_nothing_and_calls_no_provider(typed):
    store, calendar = MemoryStore(["e1", "e2"]), Calendar()

    with pytest.raises(ConfirmationMismatchError) as raised:
        await _eraser(store, calendar).erase(_tenant(), typed)

    assert raised.value.status_code == 422 and raised.value.code == "confirmation_mismatch"
    assert calendar.calls == []
    assert store.asked == [] and store.erased == []


async def test_surrounding_whitespace_is_forgiven():
    store, calendar = MemoryStore([]), Calendar()

    report = await _eraser(store, calendar).erase(_tenant(), f"  {CONFIRMATION_PHRASE}\n")

    assert report.interactions == 3
    assert len(store.erased) == 1


async def test_a_business_is_required():
    with pytest.raises(BusinessNotFoundError):
        await _eraser(MemoryStore([]), Calendar()).erase(
            Tenant(user=_tenant().user, business=None), CONFIRMATION_PHRASE
        )


async def test_every_event_is_cancelled_then_the_rows_go_for_this_business_only():
    tenant = _tenant()
    store, calendar = MemoryStore(["e1", "e2", "e3"]), Calendar()

    report = await _eraser(store, calendar).erase(tenant, CONFIRMATION_PHRASE)

    assert tenant.business is not None
    business_id = tenant.business.id
    assert calendar.calls == [(business_id, "e1"), (business_id, "e2"), (business_id, "e3")]
    assert store.erased == [(business_id, TODAY)]  # today's counters are kept
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (3, 0)
    assert (report.interactions, report.messages, report.bookings) == (3, 9, 2)
    assert (report.escalations, report.usage_events) == (1, 7)


async def test_an_event_that_is_already_gone_counts_as_cancelled():
    calendar = Calendar({"e2": CalendarNotFoundError("not_found")})

    report = await _eraser(MemoryStore(["e1", "e2", "e3"]), calendar).erase(
        _tenant(), CONFIRMATION_PHRASE
    )

    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (3, 0)
    assert len(calendar.calls) == 3


@pytest.mark.parametrize(
    "stopper",
    [
        pytest.param(CalendarAuthRevokedError("auth_revoked"), id="revoked"),
        pytest.param(CalendarNotSelectedError("not_connected"), id="not-selected"),
    ],
)
async def test_an_unusable_connection_stops_and_counts_the_rest_as_not_cancelled(stopper):
    calendar = Calendar({"e2": stopper})
    store = MemoryStore(["e1", "e2", "e3", "e4"])

    report = await _eraser(store, calendar).erase(_tenant(), CONFIRMATION_PHRASE)

    assert [event for _, event in calendar.calls] == ["e1", "e2"]  # e3, e4 never tried
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (1, 3)
    assert len(store.erased) == 1  # the data is still deleted


async def test_a_failing_event_is_counted_and_the_rest_continue():
    calendar = Calendar({"e2": CalendarUnavailableError("server_error")})

    report = await _eraser(MemoryStore(["e1", "e2", "e3"]), calendar).erase(
        _tenant(), CONFIRMATION_PHRASE
    )

    assert [event for _, event in calendar.calls] == ["e1", "e2", "e3"]
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (2, 1)


async def test_more_events_than_the_cap_are_counted_as_not_cancelled():
    calendar = Calendar()
    store = MemoryStore([f"e{n}" for n in range(1, 8)])

    report = await _eraser(store, calendar, max_cancels=5).erase(_tenant(), CONFIRMATION_PHRASE)

    assert len(calendar.calls) == 5
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (5, 2)


async def test_the_default_cap_is_fifty():
    calendar = Calendar()

    report = await _eraser(MemoryStore([f"e{n}" for n in range(60)]), calendar).erase(
        _tenant(), CONFIRMATION_PHRASE
    )

    assert len(calendar.calls) == 50
    assert report.calendar_events_not_cancelled == 10


async def test_a_spent_deadline_cancels_nothing_but_still_deletes():
    calendar, store = Calendar(), MemoryStore(["e1", "e2"])

    report = await _eraser(store, calendar, deadline_seconds=0).erase(
        _tenant(), CONFIRMATION_PHRASE
    )

    assert calendar.calls == []
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (0, 2)
    assert len(store.erased) == 1


async def test_the_deadline_is_checked_between_calls_never_inside_one():
    clock = Clock()
    calendar = Calendar(clock=clock, takes=10.0)  # each call takes 10 s of the 15 s budget
    store = MemoryStore(["e1", "e2", "e3"])

    report = await _eraser(store, calendar, deadline_seconds=15.0, monotonic=clock).erase(
        _tenant(), CONFIRMATION_PHRASE
    )

    # e1 ends at 10 s, e2 starts (10 < 15) and ends at 20 s, e3 is never started. A call that
    # runs past the deadline is allowed to finish: it is never interrupted.
    assert [event for _, event in calendar.calls] == ["e1", "e2"]
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (2, 1)
    assert len(store.erased) == 1


async def test_a_booking_confirmed_after_the_snapshot_is_reported_as_not_cancelled():
    store = MemoryStore(["e1", "e2"])

    def confirmed_meanwhile(event_id: str) -> None:
        if event_id == "e1":
            store.late_event_ids.append("late")  # a customer books while we cancel

    calendar = Calendar(on_call=confirmed_meanwhile)

    report = await _eraser(store, calendar).erase(_tenant(), CONFIRMATION_PHRASE)

    assert [event for _, event in calendar.calls] == ["e1", "e2"]  # nobody tried "late"
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (2, 1)


async def test_a_snapshot_booking_that_is_no_longer_deleted_is_not_counted():
    # Its booking was cancelled by someone else between the snapshot and the delete: the
    # rows actually deleted decide what is reported.
    store = MemoryStore(["e1"])
    calendar = Calendar({"e1": CalendarUnavailableError("server_error")})
    store.event_ids = ["e1"]

    async def erase(business_id, *, keep_usage_from):
        return ErasedCounts(0, 0, 0, 0, 0, calendar_event_ids=())

    store.erase = erase  # type: ignore[method-assign]

    report = await _eraser(store, calendar).erase(_tenant(), CONFIRMATION_PHRASE)

    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (0, 0)


async def test_no_events_means_no_provider_call():
    calendar = Calendar()

    report = await _eraser(MemoryStore([]), calendar).erase(_tenant(), CONFIRMATION_PHRASE)

    assert calendar.calls == []
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (0, 0)
