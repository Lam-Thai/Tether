"""Root `/api/v1` router."""

from fastapi import APIRouter

from api.v1.agent import router as agent_router
from api.v1.billing import router as billing_router
from api.v1.booking_rules import router as booking_rules_router
from api.v1.bookings import router as bookings_router
from api.v1.business import router as business_router
from api.v1.calendar import router as calendar_router
from api.v1.escalations import router as escalations_router
from api.v1.faqs import router as faqs_router
from api.v1.hours import router as hours_router
from api.v1.interactions import router as interactions_router
from api.v1.me import router as me_router
from api.v1.phone_numbers import router as phone_numbers_router
from api.v1.services import router as services_router
from api.v1.twilio_webhooks import router as twilio_webhooks_router
from api.v1.usage import router as usage_router

router = APIRouter(prefix="/api/v1")
router.include_router(me_router)
router.include_router(hours_router)  # before business: `/business/hours` is a literal path
router.include_router(booking_rules_router)  # literal `/business/booking-rules`
router.include_router(escalations_router)  # literal `/business/escalation-settings`
router.include_router(business_router)
router.include_router(billing_router)
router.include_router(agent_router)
router.include_router(faqs_router)
router.include_router(services_router)
router.include_router(usage_router)
router.include_router(calendar_router)
router.include_router(bookings_router)
router.include_router(interactions_router)
router.include_router(phone_numbers_router)
router.include_router(twilio_webhooks_router)
