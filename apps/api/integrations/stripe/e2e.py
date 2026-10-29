"""Billing gateway for the Playwright E2E run (`E2E_FAKE_PROVIDERS=1`, never production).

`FakeBillingGateway` with two differences, so the real web app can finish a trial with no
Stripe account:

- `create_checkout_session` returns `https://checkout.stripe.com/c/pay/<session id>`, the one
  host shape the web client follows (`apps/web/lib/api/client.ts`). The browser test
  intercepts that URL and sends the browser to `/billing/return?session_id=<id>`.
- `retrieve_checkout_session` completes an open session on first read, with a `trialing`
  subscription: the return page's sync then sees what Stripe would report after the card step.

State lives in this process (the E2E API is one long-lived uvicorn process); the
subscription itself is stored in Postgres by `services/billing`, like in production.
"""

import uuid
from datetime import UTC, datetime, timedelta

from integrations.stripe.fake import FakeBillingGateway
from integrations.stripe.port import CheckoutSessionInfo, SubscriptionInfo


class E2EBillingGateway(FakeBillingGateway):
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
        session = await super().create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            business_id=business_id,
            trial_days=trial_days,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        info = CheckoutSessionInfo(
            id=session.id,
            url=f"https://checkout.stripe.com/c/pay/{session.id}",
            status=session.status,
            customer_id=session.customer_id,
            subscription_id=session.subscription_id,
        )
        self.sessions[info.id] = info
        return info

    async def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionInfo:
        session = await super().retrieve_checkout_session(session_id)
        if session.status == "open" and session.customer_id is not None:
            calls = [c for c in self.checkout_calls if c.customer_id == session.customer_id]
            days = (calls[-1].trial_days if calls else None) or 14
            now = datetime.now(UTC)
            self.complete_checkout(
                session_id,
                SubscriptionInfo(
                    id=self.new_subscription_id(),
                    customer_id=session.customer_id,
                    status="trialing",
                    price_id=self.price.id,
                    trial_start=now,
                    trial_end=now + timedelta(days=days),
                    current_period_end=now + timedelta(days=days),
                    cancel_at_period_end=False,
                ),
            )
            session = self.sessions[session_id]
        return session

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        return f"https://billing.stripe.com/p/session/{customer_id}"
