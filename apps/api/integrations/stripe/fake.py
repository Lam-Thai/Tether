"""In-memory fake of the billing port, for tests.

Behaves like Stripe where it matters to the service: customers are
idempotent per business, sessions move open -> complete/expired, and
webhooks only parse with a valid HMAC from `sign()`.
"""

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field, replace

from integrations.stripe.port import (
    CheckoutSessionInfo,
    GatewayNotFoundError,
    InvalidWebhookSignatureError,
    PriceInfo,
    SubscriptionInfo,
    WebhookEvent,
)

FAKE_WEBHOOK_SECRET = "whsec_fake"


@dataclass
class CheckoutCall:
    customer_id: str
    price_id: str
    business_id: uuid.UUID
    trial_days: int | None
    success_url: str
    cancel_url: str


@dataclass
class FakeBillingGateway:
    price: PriceInfo = field(
        default_factory=lambda: PriceInfo(
            id="price_fake", unit_amount=2900, currency="usd", interval="month"
        )
    )
    customers: dict[uuid.UUID, str] = field(default_factory=dict)
    sessions: dict[str, CheckoutSessionInfo] = field(default_factory=dict)
    subscriptions: dict[str, SubscriptionInfo] = field(default_factory=dict)
    checkout_calls: list[CheckoutCall] = field(default_factory=list)
    expired_sessions: list[str] = field(default_factory=list)

    def _next(self, prefix: str) -> str:
        # Real Stripe ids are alphanumeric after the prefix (e.g. cs_test_a1B2...).
        return f"{prefix}_{uuid.uuid4().hex}"

    async def create_customer(self, *, business_id: uuid.UUID, email: str, name: str) -> str:
        if business_id not in self.customers:
            self.customers[business_id] = self._next("cus")
        return self.customers[business_id]

    async def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        business_id: uuid.UUID,
        trial_days: int | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSessionInfo:
        self.checkout_calls.append(
            CheckoutCall(customer_id, price_id, business_id, trial_days, success_url, cancel_url)
        )
        session_id = self._next("cs_test")
        session = CheckoutSessionInfo(
            id=session_id,
            url=f"https://checkout.stripe.test/{session_id}",
            status="open",
            customer_id=customer_id,
            subscription_id=None,
        )
        self.sessions[session_id] = session
        return session

    async def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionInfo:
        if session_id not in self.sessions:
            raise GatewayNotFoundError(session_id)
        return self.sessions[session_id]

    async def expire_checkout_session(self, session_id: str) -> None:
        self.sessions[session_id] = replace(self.sessions[session_id], status="expired")
        self.expired_sessions.append(session_id)

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionInfo:
        if subscription_id not in self.subscriptions:
            raise GatewayNotFoundError(subscription_id)
        return self.subscriptions[subscription_id]

    async def retrieve_price(self, price_id: str) -> PriceInfo:
        return self.price

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        return f"https://billing.stripe.test/{customer_id}?return={return_url}"

    def parse_webhook(self, payload: bytes, signature: str | None) -> WebhookEvent:
        if not signature or not hmac.compare_digest(signature, sign(payload)):
            raise InvalidWebhookSignatureError("bad signature")
        body = json.loads(payload)
        obj = body["data"]["object"]
        return WebhookEvent(
            id=body["id"], type=body["type"], object_id=obj["id"], customer_id=obj.get("customer")
        )

    # Test helpers: drive Stripe-side state the way a real customer would.

    def complete_checkout(self, session_id: str, subscription: SubscriptionInfo) -> None:
        self.subscriptions[subscription.id] = subscription
        self.sessions[session_id] = replace(
            self.sessions[session_id], status="complete", subscription_id=subscription.id
        )

    def new_subscription_id(self) -> str:
        return self._next("sub")


def sign(payload: bytes) -> str:
    return hmac.new(FAKE_WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()


def event_payload(event_id: str, event_type: str, object_id: str, customer_id: str | None) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "data": {"object": {"id": object_id, "customer": customer_id}},
        }
    ).encode()
