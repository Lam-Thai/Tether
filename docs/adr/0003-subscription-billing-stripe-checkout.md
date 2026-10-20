# 3. Subscription billing via Stripe-hosted Checkout

- Status: Accepted
- Date: 2026-10-20
- Issue: #28

## Context

Owners now pay for Tether: a 14-day free trial (no card) or $29/month, then the dashboard. The PRD (§2.3) listed "Payment processing" as an MVP non-goal and §13 says to avoid storing payment information. Billing is money: a false "you're subscribed", a double charge, or a webhook that silently downgrades a paying owner are all worse than no billing at all.

## Decision

- **Stripe-hosted Checkout and Billing Portal only.** The web app redirects to URLs the API returns; it has no Stripe key and no Stripe.js. No card data ever reaches Tether, so §13 still holds.
- **Stripe is the source of truth; our `subscriptions` row is a cache the entitlement check reads.** The webhook and the return-page sync both re-fetch the subscription from Stripe instead of trusting a payload, so events arriving out of order converge on the current state.
- **Two confirmation paths, one rule.** `POST /billing/checkout/{id}/sync` makes the return page fast; the webhook keeps state right afterwards (renewals, failed payments, cancellations). Neither the UI nor the API ever reports a plan as active without one of them having asked Stripe.
- **Concurrency is handled in Postgres.** Checkout creation and webhook processing take a per-business row lock. A business has at most one open Checkout Session (a new one expires the previous), so two tabs can't create two subscriptions. Webhook event ids are deduplicated in `stripe_events`.
- **Entitlement is enforced on the server.** `core.billing.get_entitled_tenant` returns 402 for anything but `trialing`, `active` or `past_due`. Every business-feature router from Slice 4 depends on it.
- **The trial is for new customers:** once a business has had any subscription, only the paid option is offered (enforced by the API, `409 trial_already_used`).
- PRD §2.3 is amended: charging owners a SaaS subscription is in scope; processing payments for owners' customers remains a non-goal.

## Alternatives considered

- **Embedded Stripe Elements.** More control over the page, but puts Stripe.js and a publishable key in the app and widens PCI scope. Rejected.
- **Trust webhook payloads directly.** Fewer API calls, but Stripe doesn't guarantee ordering; a late `created` event could overwrite a `canceled` state.
- **Webhook-only confirmation.** Simpler, but the return page would depend on webhook latency and on `stripe listen` running locally.
- **Card-required trial.** Better conversion signal, more signup friction. The trial uses `payment_method_collection: if_required` and cancels itself at the end if no card was added.

## Consequences

- The API needs Stripe settings to start (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`, `APP_BASE_URL`); CI uses placeholders and a fake gateway.
- Stripe calls run under a row lock inside the request transaction. Fine at this scale; revisit if checkout latency grows.
- `UsageGuard` doesn't exist yet. Until #5, Stripe calls rely on bounded SDK retries, idempotency keys and a 15s timeout; #5 must wrap `BillingGateway`.
