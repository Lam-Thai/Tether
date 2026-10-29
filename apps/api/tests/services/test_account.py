"""`AccountEraser`: the confirmation phrase, the order of side effects (Stripe, then Google
Calendar, then the database, then Clerk), what aborts the deletion and what is only reported.
The Postgres deletes and the whole flow over HTTP are `tests/api/test_account.py`."""

import logging
import uuid
from datetime import UTC, datetime

import pytest

from core.tenant import Tenant
from integrations.stripe.port import GatewayNotFoundError
from models.business import Business
from models.user import User
from services.account import (
    CONFIRMATION_PHRASE,
    AccountErased,
    AccountEraser,
    BillingUnreachableError,
)
from services.calendar import (
    CalendarAuthRevokedError,
    CalendarNotConfiguredError,
    CalendarNotConnectedError,
    CalendarNotFoundError,
    CalendarNotSelectedError,
    CalendarUnavailableError,
)
from services.identity import IdentityError
from services.test_data import ConfirmationMismatchError

NOW = datetime(2031, 6, 1, 12, tzinfo=UTC)


class Journal:
    """Everything the eraser did, in order, across every collaborator."""

    def __init__(self) -> None:
        self.steps: list[str] = []

    def index(self, step: str) -> int:
        return self.steps.index(step)

    def count(self, prefix: str) -> int:
        return sum(1 for step in self.steps if step.startswith(prefix))


class MemoryStore:
    def __init__(self, journal: Journal, *, customer: str | None, event_ids: list[str]) -> None:
        self.journal = journal
        self.customer = customer
        self.event_ids = event_ids
        # Upcoming events of bookings that appear after the snapshot but before the delete.
        self.late_event_ids: list[str] = []
        self.deleted: list[tuple[uuid.UUID, datetime]] = []

    async def stripe_customer_id(self, business_id: uuid.UUID) -> str | None:
        self.journal.steps.append("read_customer")
        return self.customer

    async def upcoming_event_ids(self, business_id: uuid.UUID, *, from_time: datetime) -> list[str]:
        self.journal.steps.append("read_events")
        assert from_time == NOW
        return list(self.event_ids)

    async def delete_account(self, user_id: uuid.UUID, *, upcoming_from: datetime) -> AccountErased:
        self.journal.steps.append("db_delete")
        self.deleted.append((user_id, upcoming_from))
        return AccountErased(
            businesses=1,
            bookings=4,
            calendar_event_ids=tuple([*self.event_ids, *self.late_event_ids]),
        )

    async def commit(self) -> None:
        self.journal.steps.append("commit")


class Stripe:
    def __init__(self, journal: Journal, failure: Exception | None = None) -> None:
        self.journal = journal
        self.failure = failure
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def __call__(self, business_id: uuid.UUID, customer_id: str) -> None:
        self.journal.steps.append("stripe_delete")
        self.calls.append((business_id, customer_id))
        if self.failure is not None:
            raise self.failure


class Calendar:
    """A scripted `cancel_event`; `outcomes[event_id]` is an exception to raise."""

    def __init__(self, journal: Journal, outcomes: dict[str, Exception] | None = None) -> None:
        self.journal = journal
        self.outcomes = outcomes or {}
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def __call__(self, business_id: uuid.UUID, event_id: str) -> None:
        self.journal.steps.append(f"cancel:{event_id}")
        self.calls.append((business_id, event_id))
        if event_id in self.outcomes:
            raise self.outcomes[event_id]


class Disconnect:
    def __init__(self, journal: Journal, outcome: bool | Exception = True) -> None:
        self.journal = journal
        self.outcome = outcome
        self.calls: list[uuid.UUID] = []

    async def __call__(self, business_id: uuid.UUID) -> bool:
        self.journal.steps.append("disconnect")
        self.calls.append(business_id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Identity:
    def __init__(self, journal: Journal, failure: Exception | None = None) -> None:
        self.journal = journal
        self.failure = failure
        self.deleted: list[str] = []

    async def delete_user(self, external_auth_id: str) -> None:
        self.journal.steps.append("clerk_delete")
        if self.failure is not None:
            raise self.failure
        self.deleted.append(external_auth_id)


class Rig:
    def __init__(
        self,
        *,
        customer: str | None = "cus_1",
        event_ids: list[str] | None = None,
        stripe_failure: Exception | None = None,
        outcomes: dict[str, Exception] | None = None,
        disconnect: bool | Exception = True,
        identity_failure: Exception | None = None,
        **limits,
    ) -> None:
        self.journal = Journal()
        self.store = MemoryStore(self.journal, customer=customer, event_ids=event_ids or [])
        self.stripe = Stripe(self.journal, stripe_failure)
        self.calendar = Calendar(self.journal, outcomes)
        self.disconnect = Disconnect(self.journal, disconnect)
        self.identity = Identity(self.journal, identity_failure)
        self.eraser = AccountEraser(
            self.store,
            self.stripe,
            self.calendar,
            self.disconnect,
            self.identity,
            lambda: NOW,
            **limits,
        )


def _tenant(*, with_business: bool = True) -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_2abc", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone="UTC",
    )
    return Tenant(user=user, business=business if with_business else None)


@pytest.mark.parametrize(
    "typed",
    [
        pytest.param("", id="empty"),
        pytest.param("delete my account", id="lower-case"),
        pytest.param("DELETE", id="partial"),
        pytest.param("DELETE MY ACCOUNT NOW", id="extra-words"),
        pytest.param("DELETE  MY ACCOUNT", id="inner-double-space"),
        pytest.param("DELETE TEST DATA", id="the-other-phrase"),
    ],
)
async def test_a_mismatch_touches_nothing(typed):
    rig = Rig(event_ids=["e1"])

    with pytest.raises(ConfirmationMismatchError) as raised:
        await rig.eraser.erase(_tenant(), typed)

    assert raised.value.status_code == 422 and raised.value.code == "confirmation_mismatch"
    assert rig.journal.steps == []


async def test_surrounding_whitespace_is_forgiven():
    rig = Rig()

    report = await rig.eraser.erase(_tenant(), f"  {CONFIRMATION_PHRASE}\n")

    assert report.sign_in_deleted is True
    assert rig.store.deleted != []


async def test_everything_goes_in_order_stripe_calendar_database_then_clerk():
    tenant = _tenant()
    rig = Rig(event_ids=["e1", "e2"])

    report = await rig.eraser.erase(tenant, CONFIRMATION_PHRASE)

    assert tenant.business is not None
    assert rig.stripe.calls == [(tenant.business.id, "cus_1")]
    assert rig.calendar.calls == [(tenant.business.id, "e1"), (tenant.business.id, "e2")]
    assert rig.disconnect.calls == [tenant.business.id]
    assert rig.store.deleted == [(tenant.user.id, NOW)]
    assert rig.identity.deleted == ["user_2abc"]
    journal = rig.journal
    # A commit precedes the Stripe call, so no transaction is held across it.
    assert journal.index("commit") < journal.index("stripe_delete")
    assert journal.index("stripe_delete") < journal.index("cancel:e1")
    assert journal.index("cancel:e2") < journal.index("disconnect")
    assert journal.index("disconnect") < journal.index("db_delete")
    # The database delete is committed before Clerk is called.
    assert journal.steps[-2:] == ["commit", "clerk_delete"]
    assert journal.index("db_delete") < len(journal.steps) - 2
    assert report.calendar_events_cancelled == 2 and report.calendar_events_not_cancelled == 0
    assert report.calendar_token_revoked is True and report.sign_in_deleted is True


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(RuntimeError("stripe is down"), id="outage"),
        pytest.param(TimeoutError(), id="timeout"),
    ],
)
async def test_a_stripe_failure_aborts_before_anything_else_is_touched(failure):
    rig = Rig(event_ids=["e1"], stripe_failure=failure)

    with pytest.raises(BillingUnreachableError) as raised:
        await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert raised.value.status_code == 503 and raised.value.code == "billing_unreachable"
    assert "nothing was deleted" in raised.value.message
    assert rig.calendar.calls == [] and rig.disconnect.calls == []
    assert rig.store.deleted == [] and rig.identity.deleted == []
    assert rig.journal.count("clerk") == 0 and rig.journal.count("db_delete") == 0


async def test_a_stripe_customer_that_is_already_gone_counts_as_deleted():
    rig = Rig(event_ids=["e1"], stripe_failure=GatewayNotFoundError("cus_1"))

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert rig.calendar.calls != [] and rig.store.deleted != []
    assert report.sign_in_deleted is True


async def test_no_stripe_customer_means_no_stripe_call():
    rig = Rig(customer=None)

    await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert rig.stripe.calls == []
    assert rig.store.deleted != []


async def test_without_a_business_only_the_user_and_the_sign_in_go():
    tenant = _tenant(with_business=False)
    rig = Rig()

    report = await rig.eraser.erase(tenant, CONFIRMATION_PHRASE)

    assert rig.stripe.calls == [] and rig.calendar.calls == [] and rig.disconnect.calls == []
    assert rig.journal.steps == ["db_delete", "commit", "clerk_delete"]
    assert rig.store.deleted == [(tenant.user.id, NOW)]
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (0, 0)
    assert report.calendar_token_revoked is None and report.sign_in_deleted is True


async def test_an_event_that_is_already_gone_counts_as_cancelled():
    rig = Rig(event_ids=["e1", "e2"], outcomes={"e2": CalendarNotFoundError("not_found")})

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (2, 0)


@pytest.mark.parametrize(
    "stopper",
    [
        pytest.param(CalendarAuthRevokedError("auth_revoked"), id="revoked"),
        pytest.param(CalendarNotSelectedError("not_connected"), id="not-selected"),
    ],
)
async def test_an_unusable_connection_stops_and_the_data_is_still_deleted(stopper):
    rig = Rig(event_ids=["e1", "e2", "e3", "e4"], outcomes={"e2": stopper})

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert [event for _, event in rig.calendar.calls] == ["e1", "e2"]  # e3, e4 never tried
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (1, 3)
    assert rig.store.deleted != [] and rig.identity.deleted == ["user_2abc"]


async def test_a_failing_event_is_counted_and_the_rest_continue():
    rig = Rig(
        event_ids=["e1", "e2", "e3"], outcomes={"e2": CalendarUnavailableError("server_error")}
    )

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert [event for _, event in rig.calendar.calls] == ["e1", "e2", "e3"]
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (2, 1)


async def test_more_events_than_the_cap_are_counted_as_not_cancelled():
    rig = Rig(event_ids=[f"e{n}" for n in range(1, 8)], max_cancels=5)

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert len(rig.calendar.calls) == 5
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (5, 2)


async def test_a_spent_deadline_cancels_nothing_but_still_deletes():
    rig = Rig(event_ids=["e1", "e2"], deadline_seconds=0)

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert rig.calendar.calls == []
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (0, 2)
    assert rig.store.deleted != []


async def test_an_upcoming_booking_created_after_the_snapshot_is_reported_not_dropped():
    rig = Rig(event_ids=["e1"])
    rig.store.late_event_ids.append("late")  # a customer books while we work

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert [event for _, event in rig.calendar.calls] == ["e1"]  # nobody tried "late"
    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (1, 1)


async def test_a_snapshot_event_whose_booking_is_no_longer_deleted_is_not_counted():
    rig = Rig(event_ids=["e1"], outcomes={"e1": CalendarUnavailableError("server_error")})

    async def delete_account(user_id, *, upcoming_from):
        return AccountErased(businesses=1, bookings=0, calendar_event_ids=())

    rig.store.delete_account = delete_account  # type: ignore[method-assign]

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert (report.calendar_events_cancelled, report.calendar_events_not_cancelled) == (0, 0)


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(False, id="google-could-not-confirm"),
        pytest.param(CalendarUnavailableError("server_error"), id="calendar-error"),
        pytest.param(CalendarAuthRevokedError("auth_revoked"), id="revoked"),
    ],
)
async def test_a_token_that_could_not_be_revoked_is_reported_never_raised(outcome):
    rig = Rig(disconnect=outcome)

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert report.calendar_token_revoked is False
    assert rig.store.deleted != [] and rig.identity.deleted == ["user_2abc"]


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(CalendarNotConfiguredError(), id="not-configured"),
        pytest.param(CalendarNotConnectedError(), id="no-connection"),
    ],
)
async def test_no_calendar_connection_reports_no_token_rather_than_a_failed_revoke(outcome):
    # `None`, not False: the UI warns the owner to remove Google access only on False.
    rig = Rig(disconnect=outcome)

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert report.calendar_token_revoked is None
    assert rig.store.deleted != [] and rig.identity.deleted == ["user_2abc"]


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(IdentityError("transport"), id="clerk-down"),
        pytest.param(IdentityError("not_configured"), id="not-configured"),
        pytest.param(RuntimeError("anything else"), id="unexpected"),
    ],
)
async def test_a_clerk_failure_is_reported_never_raised_and_the_data_stays_deleted(failure):
    rig = Rig(identity_failure=failure)

    report = await rig.eraser.erase(_tenant(), CONFIRMATION_PHRASE)

    assert report.sign_in_deleted is False
    assert rig.store.deleted != []
    assert rig.journal.steps[-1] == "clerk_delete"


async def test_the_log_line_has_ids_and_counts_but_no_email_or_secret(caplog):
    tenant = _tenant()
    rig = Rig(event_ids=["e1"], identity_failure=IdentityError("transport"))

    with caplog.at_level(logging.INFO, logger="tether.account"):
        await rig.eraser.erase(tenant, CONFIRMATION_PHRASE)

    (record,) = [r for r in caplog.records if r.name == "tether.account"]
    assert record.getMessage() == "account deleted"
    assert record.__dict__["sign_in_error"] == "transport"
    assert record.__dict__["events_cancelled"] == 1
    assert record.__dict__["user_id"] == str(tenant.user.id)
    rendered = repr(record.__dict__)
    assert "owner@example.com" not in rendered and "user_2abc" not in rendered
