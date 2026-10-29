"""Root `/api/v1` router."""

from fastapi import APIRouter, Depends

from api.v1.account import router as account_router
from api.v1.agent import router as agent_router
from api.v1.billing import router as billing_router
from api.v1.booking_rules import router as booking_rules_router
from api.v1.bookings import router as bookings_router
from api.v1.business import router as business_router
from api.v1.calendar import router as calendar_router
from api.v1.dashboard import router as dashboard_router
from api.v1.escalations import router as escalations_router
from api.v1.faqs import router as faqs_router
from api.v1.hours import router as hours_router
from api.v1.interactions import router as interactions_router
from api.v1.me import router as me_router
from api.v1.media import router as media_router
from api.v1.phone_numbers import router as phone_numbers_router
from api.v1.services import router as services_router
from api.v1.twilio_webhooks import router as twilio_webhooks_router
from api.v1.usage import router as usage_router
from core.rate_limit import limit_tenant_mutations

router = APIRouter(prefix="/api/v1")
# Every router of a signed-in owner counts its mutations per business (slice 17). Not the
# webhooks (per IP, in their own routers), billing (mixes both: per route) or media (GET).
owner_limits = [Depends(limit_tenant_mutations)]
router.include_router(me_router, dependencies=owner_limits)
router.include_router(account_router, dependencies=owner_limits)
router.include_router(hours_router, dependencies=owner_limits)  # before business: literal path
router.include_router(booking_rules_router, dependencies=owner_limits)  # literal path
router.include_router(escalations_router, dependencies=owner_limits)  # literal path
router.include_router(dashboard_router, dependencies=owner_limits)  # literal path
router.include_router(business_router, dependencies=owner_limits)
router.include_router(billing_router)
router.include_router(agent_router, dependencies=owner_limits)
router.include_router(faqs_router, dependencies=owner_limits)
router.include_router(services_router, dependencies=owner_limits)
router.include_router(usage_router, dependencies=owner_limits)
router.include_router(calendar_router, dependencies=owner_limits)
router.include_router(bookings_router, dependencies=owner_limits)
router.include_router(interactions_router, dependencies=owner_limits)
router.include_router(phone_numbers_router, dependencies=owner_limits)
router.include_router(twilio_webhooks_router)  # per-IP limit on the router itself
router.include_router(media_router)  # public: the unguessable token is the capability
