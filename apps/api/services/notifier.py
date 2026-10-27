"""The `Notifier` port: how an escalation alert reaches the owner (slice 13).

`EscalationService` (`services/escalation.py`) depends on this port only.
Implementations:

- `LogNotifier` (here): demo mode. Nothing is sent; the alert is logged as fixed
  codes and the escalation's `sms_status` becomes `logged`. Used whenever Twilio isn't
  configured.
- `TwilioSmsNotifier` (`services/twilio_sms.py`): one SMS through the Twilio port,
  metered as `UsageKind.SMS` with `retry_bounded` around `usage.metered`.

An `Alert` carries no customer PII: a reason category and an inbox link only. The
body is built by `alert_body`, the only place the SMS text is written.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from services.escalation_policy import Reason

logger = logging.getLogger("tether.notifier")

# What the alert says the customer needs help with, by reason.
REASON_PHRASES: dict[Reason, str] = {
    "urgent": "an urgent matter",
    "human_requested": "a request to speak to a person",
    "complaint": "a complaint",
    "change_request": "an appointment change",
    "booking_unresolved": "a booking that couldn't be completed",
    "integration_failing": "a technical problem",
    "clarification_exhausted": "a question the agent couldn't answer",
}


@dataclass(frozen=True)
class Alert:
    business_id: uuid.UUID
    interaction_id: uuid.UUID
    escalation_id: uuid.UUID
    reason: Reason
    # E.164 owner phone from `escalation_settings.contact_phone`.
    to: str
    # Absolute link to the interaction in the web inbox.
    inbox_url: str


def alert_body(reason: Reason, inbox_url: str) -> str:
    return (
        f"Tether alert: A customer needs help with {REASON_PHRASES[reason]}. "
        f"Open your Tether inbox to review: {inbox_url}"
    )


# `logged`: demo mode, nothing sent. The rest are Twilio's message statuses at send time.
SendStatus = Literal["logged", "accepted", "queued", "sending", "sent"]


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    # Twilio's MessageSid; None in demo mode.
    provider_id: str | None = None


class NotifierError(Exception):
    """The alert was not sent (or its sending is unconfirmed). `code` is a fixed reason
    (`limit_reached`, `usage_unavailable`, `invalid_number`, `send_unconfirmed`,
    `provider_error`, ...), never provider text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Notifier(Protocol):
    async def send_alert(self, alert: Alert) -> SendResult:
        """Send one alert. Raises `NotifierError` when it was not (certainly) sent."""
        ...


class LogNotifier:
    """Demo mode: records that an alert would have gone out. No PII in the log line."""

    async def send_alert(self, alert: Alert) -> SendResult:
        logger.info(
            "escalation alert logged (demo mode)",
            extra={
                "business_id": str(alert.business_id),
                "escalation_id": str(alert.escalation_id),
                "reason": alert.reason,
            },
        )
        return SendResult(status="logged")
