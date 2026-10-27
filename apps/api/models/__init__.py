"""Import every model so `Base.metadata` is fully populated for Alembic
autogenerate and `Base.metadata.create_all` (tests) as soon as anything
imports `models` or `models.base`.
"""

from models.agent import Agent
from models.billing import StripeEvent, Subscription
from models.booking import Booking
from models.booking_rules import BookingRulesRow
from models.business import Business
from models.calendar import CalendarConnection
from models.escalation import Escalation, EscalationSettingsRow
from models.faq import Faq
from models.hours import BusinessHours
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation
from models.phone_number import PhoneNumber
from models.service import Service
from models.usage import UsageCounter, UsageEvent
from models.user import User
from models.voice_turn import VoiceTurn

__all__ = [
    "Agent",
    "Booking",
    "BookingRulesRow",
    "Business",
    "BusinessHours",
    "CalendarConnection",
    "Escalation",
    "EscalationSettingsRow",
    "Faq",
    "Interaction",
    "InteractionMessage",
    "InteractionOperation",
    "PhoneNumber",
    "Service",
    "StripeEvent",
    "Subscription",
    "UsageCounter",
    "UsageEvent",
    "User",
    "VoiceTurn",
]
