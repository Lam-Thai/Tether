"""Unit tests for the billing service: in-memory store + fake Stripe, no DB."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.tenant import Tenant
from integrations.stripe.fake import FakeBillingGateway, event_payload, sign
from integrations.stripe.port import SubscriptionInfo
from models.billing import Subscription
from models.business import Business
from models.user import User
from services import billing
from services.billing import BillingConfig
from services.business import BusinessNotFoundError

CONFIG = BillingConfig(price_id="price_fake", trial_days=14, app_base_url="https://app.test")
NOW = datetime(2026, 10, 20, tzinfo=UTC)


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, Subscription] = {}
        self.events: set[str] = set()

    async def get_for_business(self, business_id):
        return self.rows.get(business_id)

    async def lock_for_business(self, business_id):
        if business_id not in self.rows:
            self.rows[business_id] = Subscription(
                business_id=business_id, cancel_at_period_end=False
            )
        return self.rows[business_id]

    async def lock_for_customer(self, customer_id):
        return next((r for r in self.rows.values() if r.stripe_customer_id == customer_id), None)

    async def record_event(self, event_id, event_type):
        if event_id in self.events:
            return False
        self.events.add(event_id)
        return True

    async def flush(self):
        pass


def _tenant(with_business: bool = True) -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = (
        Business(id=uuid.uuid4(), owner_id=user.id, name="Alpha Salon") if with_business else None
    )
    return Tenant(user=user, business=business)


def _sub(sub_id: str, customer_id: str, status: str, *, trial: bool = False) -> SubscriptionInfo:
    return SubscriptionInfo(
        id=sub_id,
        customer_id=customer_id,
        status=status,
        price_id="price_fake",
        trial_start=NOW if trial else None,
        trial_end=NOW + timedelta(days=14) if trial else None,
        current_period_end=NOW + timedelta(days=30),
        cancel_at_period_end=False,
    )


def _bid(tenant: Tenant) -> uuid.UUID:
    assert tenant.business is not None
    return tenant.business.id


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def gateway():
    return FakeBillingGateway()


async def _complete_trial(store, gateway, tenant) -> Subscription:
    await billing.start_checkout(store, gateway, CONFIG, tenant, "trial")
    row = store.rows[_bid(tenant)]
    gateway.complete_checkout(
        row.pending_checkout_session_id,
        _sub(gateway.new_subscription_id(), row.stripe_customer_id, "trialing", trial=True),
    )
    await billing.sync_checkout(store, gateway, tenant, row.pending_checkout_session_id)
    return row


# access_for: the entitlement policy


@pytest.mark.parametrize(
    ("status", "entitled"),
    [
        ("trialing", True),
        ("active", True),
        ("past_due", True),
        ("canceled", False),
        ("unpaid", False),
        ("incomplete", False),
        ("incomplete_expired", False),
        ("paused", False),
        (None, False),
    ],
)
def test_access_for_each_stripe_status(status, entitled):
    row = Subscription(business_id=uuid.uuid4(), status=status, cancel_at_period_end=False)
    assert billing.access_for(row).entitled is entitled


def test_access_for_no_subscription_is_not_entitled_but_can_trial():
    access = billing.access_for(None)
    assert access.entitled is False
    assert access.trial_available is True


# start_checkout


async def test_trial_checkout_sets_trial_days_and_return_urls(store, gateway):
    tenant = _tenant()

    url = await billing.start_checkout(store, gateway, CONFIG, tenant, "trial")

    call = gateway.checkout_calls[-1]
    assert call.trial_days == 14
    assert call.business_id == _bid(tenant)
    assert call.success_url == "https://app.test/billing/return?session_id={CHECKOUT_SESSION_ID}"
    assert call.cancel_url == "https://app.test/billing/plan?canceled=1"
    row = store.rows[_bid(tenant)]
    assert url == gateway.sessions[row.pending_checkout_session_id].url


async def test_paid_checkout_has_no_trial(store, gateway):
    await billing.start_checkout(store, gateway, CONFIG, _tenant(), "paid")
    assert gateway.checkout_calls[-1].trial_days is None


async def test_checkout_needs_a_business(store, gateway):
    with pytest.raises(BusinessNotFoundError):
        await billing.start_checkout(store, gateway, CONFIG, _tenant(with_business=False), "trial")


async def test_new_checkout_expires_the_previous_open_one(store, gateway):
    tenant = _tenant()
    await billing.start_checkout(store, gateway, CONFIG, tenant, "trial")
    first = store.rows[_bid(tenant)].pending_checkout_session_id

    await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")

    assert gateway.expired_sessions == [first]
    assert store.rows[_bid(tenant)].pending_checkout_session_id != first


async def test_checkout_refused_when_a_previous_session_already_completed(store, gateway):
    """Two tabs: the first one finished paying before the webhook landed."""
    tenant = _tenant()
    await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")
    row = store.rows[_bid(tenant)]
    gateway.complete_checkout(
        row.pending_checkout_session_id, _sub("sub_live", row.stripe_customer_id, "active")
    )

    with pytest.raises(billing.SubscriptionActiveError):
        await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")
    assert len(gateway.checkout_calls) == 1


async def test_second_trial_is_refused(store, gateway):
    tenant = _tenant()
    row = await _complete_trial(store, gateway, tenant)
    row.status = "canceled"

    with pytest.raises(billing.TrialAlreadyUsedError):
        await billing.start_checkout(store, gateway, CONFIG, tenant, "trial")


async def test_paid_checkout_still_allowed_after_trial_ends(store, gateway):
    tenant = _tenant()
    row = await _complete_trial(store, gateway, tenant)
    row.status = "canceled"

    assert await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")


async def test_entitled_business_cannot_start_another_checkout(store, gateway):
    tenant = _tenant()
    await _complete_trial(store, gateway, tenant)

    with pytest.raises(billing.SubscriptionActiveError):
        await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")


async def test_customer_is_created_once_per_business(store, gateway):
    tenant = _tenant()
    await billing.start_checkout(store, gateway, CONFIG, tenant, "trial")
    await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")

    assert len(gateway.customers) == 1
    customer_ids = {c.customer_id for c in gateway.checkout_calls}
    assert customer_ids == {gateway.customers[_bid(tenant)]}


# sync_checkout


async def test_sync_after_trial_checkout_is_entitled(store, gateway):
    tenant = _tenant()
    await billing.start_checkout(store, gateway, CONFIG, tenant, "trial")
    row = store.rows[_bid(tenant)]
    session_id = row.pending_checkout_session_id
    gateway.complete_checkout(
        session_id, _sub("sub_1", row.stripe_customer_id, "trialing", trial=True)
    )

    access = await billing.sync_checkout(store, gateway, tenant, session_id)

    assert access.entitled is True
    assert access.status == "trialing"
    assert access.trial_ends_at == NOW + timedelta(days=14)
    assert access.trial_available is False
    assert row.pending_checkout_session_id is None


async def test_sync_of_an_open_session_is_not_entitled(store, gateway):
    tenant = _tenant()
    await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")
    session_id = store.rows[_bid(tenant)].pending_checkout_session_id

    access = await billing.sync_checkout(store, gateway, tenant, session_id)

    assert access.entitled is False


async def test_sync_rejects_another_businesses_session(store, gateway):
    owner, intruder = _tenant(), _tenant()
    await billing.start_checkout(store, gateway, CONFIG, owner, "paid")
    await billing.start_checkout(store, gateway, CONFIG, intruder, "paid")
    owners_session = store.rows[_bid(owner)].pending_checkout_session_id

    with pytest.raises(billing.CheckoutNotFoundError):
        await billing.sync_checkout(store, gateway, intruder, owners_session)


async def test_sync_of_unknown_session_is_404(store, gateway):
    tenant = _tenant()
    await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")

    with pytest.raises(billing.CheckoutNotFoundError):
        await billing.sync_checkout(store, gateway, tenant, "cs_test_doesnotexist")


# apply_event


def _signed(event_id, event_type, object_id, customer_id):
    payload = event_payload(event_id, event_type, object_id, customer_id)
    return payload, sign(payload)


async def _open_paid_checkout(store, gateway):
    tenant = _tenant()
    await billing.start_checkout(store, gateway, CONFIG, tenant, "paid")
    return tenant, store.rows[_bid(tenant)]


async def test_webhook_with_bad_signature_is_rejected(store, gateway):
    payload = event_payload("evt_1", "customer.subscription.updated", "sub_1", "cus_1")
    with pytest.raises(billing.InvalidWebhookError):
        await billing.apply_event(store, gateway, payload, "not-the-signature")
    with pytest.raises(billing.InvalidWebhookError):
        await billing.apply_event(store, gateway, payload, None)


async def test_checkout_completed_webhook_activates_the_subscription(store, gateway):
    _, row = await _open_paid_checkout(store, gateway)
    session_id = row.pending_checkout_session_id
    gateway.complete_checkout(session_id, _sub("sub_1", row.stripe_customer_id, "active"))

    outcome = await billing.apply_event(
        store,
        gateway,
        *_signed("evt_1", "checkout.session.completed", session_id, row.stripe_customer_id),
    )

    assert outcome == "applied"
    assert row.status == "active"
    assert row.pending_checkout_session_id is None


async def test_redelivered_webhook_is_processed_once(store, gateway):
    _, row = await _open_paid_checkout(store, gateway)
    gateway.complete_checkout(
        row.pending_checkout_session_id, _sub("sub_1", row.stripe_customer_id, "active")
    )
    args = _signed("evt_1", "customer.subscription.updated", "sub_1", row.stripe_customer_id)

    assert await billing.apply_event(store, gateway, *args) == "applied"
    row.status = "sentinel"
    assert await billing.apply_event(store, gateway, *args) == "duplicate"
    assert row.status == "sentinel"


async def test_out_of_order_events_converge_on_stripes_current_state(store, gateway):
    """`updated` (canceled) arrives before the older `created`: both re-fetch."""
    _, row = await _open_paid_checkout(store, gateway)
    customer = row.stripe_customer_id
    gateway.subscriptions["sub_1"] = _sub("sub_1", customer, "canceled")

    await billing.apply_event(
        store, gateway, *_signed("evt_2", "customer.subscription.updated", "sub_1", customer)
    )
    await billing.apply_event(
        store, gateway, *_signed("evt_1", "customer.subscription.created", "sub_1", customer)
    )

    assert row.status == "canceled"


async def test_stale_event_for_a_dead_subscription_keeps_the_live_one(store, gateway):
    _, row = await _open_paid_checkout(store, gateway)
    customer = row.stripe_customer_id
    gateway.subscriptions["sub_new"] = _sub("sub_new", customer, "active")
    gateway.subscriptions["sub_old"] = _sub("sub_old", customer, "canceled")
    await billing.apply_event(
        store, gateway, *_signed("evt_1", "customer.subscription.created", "sub_new", customer)
    )

    await billing.apply_event(
        store, gateway, *_signed("evt_2", "customer.subscription.deleted", "sub_old", customer)
    )

    assert row.stripe_subscription_id == "sub_new"
    assert row.status == "active"


async def test_subscription_deleted_webhook_revokes_access(store, gateway):
    tenant = _tenant()
    row = await _complete_trial(store, gateway, tenant)
    sub_id = row.stripe_subscription_id
    assert sub_id is not None and row.stripe_customer_id is not None
    gateway.subscriptions[sub_id] = _sub(sub_id, row.stripe_customer_id, "canceled")

    await billing.apply_event(
        store,
        gateway,
        *_signed("evt_9", "customer.subscription.deleted", sub_id, row.stripe_customer_id),
    )

    assert billing.access_for(row).entitled is False


async def test_webhook_for_unknown_customer_or_type_is_ignored(store, gateway):
    assert (
        await billing.apply_event(
            store, gateway, *_signed("evt_1", "customer.subscription.updated", "sub_x", "cus_x")
        )
        == "ignored"
    )
    invoice = _signed("evt_2", "invoice.paid", "in_1", "cus_x")
    assert await billing.apply_event(store, gateway, *invoice) == "ignored"


async def test_webhook_whose_subscription_belongs_to_another_customer_is_ignored(store, gateway):
    _, row = await _open_paid_checkout(store, gateway)
    gateway.subscriptions["sub_other"] = _sub("sub_other", "cus_someone_else", "active")

    await billing.apply_event(
        store,
        gateway,
        *_signed("evt_1", "customer.subscription.updated", "sub_other", row.stripe_customer_id),
    )

    assert row.status is None
    assert row.stripe_subscription_id is None


async def test_completed_session_whose_subscription_has_another_customer_is_ignored(
    store, gateway
):
    tenant, row = await _open_paid_checkout(store, gateway)
    session_id = row.pending_checkout_session_id
    assert session_id is not None
    gateway.complete_checkout(session_id, _sub("sub_x", "cus_someone_else", "active"))

    access = await billing.sync_checkout(store, gateway, tenant, session_id)

    assert access.entitled is False


async def test_a_second_live_subscription_is_tracked_and_logged(store, gateway, caplog):
    _, row = await _open_paid_checkout(store, gateway)
    customer = row.stripe_customer_id
    assert customer is not None
    gateway.subscriptions["sub_a"] = _sub("sub_a", customer, "active")
    gateway.subscriptions["sub_b"] = _sub("sub_b", customer, "active")
    await billing.apply_event(
        store, gateway, *_signed("evt_a", "customer.subscription.created", "sub_a", customer)
    )

    await billing.apply_event(
        store, gateway, *_signed("evt_b", "customer.subscription.created", "sub_b", customer)
    )

    assert row.stripe_subscription_id == "sub_b"
    assert "two live subscriptions" in caplog.text


async def test_event_for_unknown_customer_is_not_recorded(store, gateway):
    args = _signed("evt_early", "customer.subscription.updated", "sub_1", "cus_later")
    assert await billing.apply_event(store, gateway, *args) == "ignored"
    assert "evt_early" not in store.events


# open_portal / get_plan


async def test_portal_needs_a_billing_account(store, gateway):
    with pytest.raises(billing.BillingAccountNotFoundError):
        await billing.open_portal(store, gateway, CONFIG, _tenant())


async def test_portal_returns_to_the_dashboard(store, gateway):
    tenant, _ = await _open_paid_checkout(store, gateway)
    url = await billing.open_portal(store, gateway, CONFIG, tenant)
    assert url.endswith("?return=https://app.test/dashboard")


async def test_plan_reports_price_and_trial_availability(store, gateway):
    tenant = _tenant()
    plan = await billing.get_plan(store, gateway, CONFIG, tenant)
    assert (plan.amount, plan.currency, plan.interval) == (2900, "usd", "month")
    assert plan.trial_days == 14
    assert plan.trial_available is True

    await _complete_trial(store, gateway, tenant)
    assert (await billing.get_plan(store, gateway, CONFIG, tenant)).trial_available is False
