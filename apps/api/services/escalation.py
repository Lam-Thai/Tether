"""`EscalationService`: record an escalation once, alert the owner once, track the alert.

The policy (`services/escalation_policy.py`) decides THAT a turn needs a person and why;
this module owns what happens next:

- `record` stores the escalation, insert-or-get on `(interaction_id, reason)`, so a
  reason detected on every later turn is still ONE record. The initial `sms_status` is
  `disabled` (owner has SMS off, or no contact phone) or `pending`; the store stores
  `skipped` instead when the interaction already has an escalation (one SMS per
  interaction, whatever the reasons).
- `notify` claims `pending -> sending` with a conditional update, commits, and only then
  calls the `Notifier` (no transaction is held across a provider call). The outcome is
  stored as `logged` (demo mode), the provider's status, or `failed` plus a fixed code.
  A notifier problem never reaches the caller: the customer's turn must not fail
  because an SMS could not be sent, and the reply never depends on it.
- `escalate` is `record` + commit + `notify`. The conversation engine uses the two
  steps separately so `record` runs inside its turn transaction.
- `apply_sms_status` applies Twilio's delivery callbacks monotonically (a repeat or an
  older status is a no-op), looked up by message sid.

Alert text carries no customer data (a reason category and an inbox link), and the log
carries fixed codes and ids only. No FastAPI or SQLAlchemy imports.
"""

import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from core.errors import AppError
from core.tenant import Tenant
from services.agent import require_business
from services.escalation_policy import (
    DEFAULT_CLARIFICATION_THRESHOLD,
    MAX_CLARIFICATION_THRESHOLD,
    MAX_URGENT_KEYWORD_CHARS,
    MAX_URGENT_KEYWORDS,
    MIN_CLARIFICATION_THRESHOLD,
    EscalationDecision,
    EscalationRules,
)
from services.notifier import Alert, Notifier, NotifierError

logger = logging.getLogger("tether.escalation")

StatusFilter = Literal["open", "resolved", "all"]
LIST_LIMIT = 100
# Reasons triggered by THIS turn's customer message: a repeat after the owner resolved the
# escalation reopens it. The history-based reasons (`booking_unresolved`,
# `integration_failing`, `clarification_exhausted`) stay resolved: their evidence is
# still in the interaction and would otherwise reopen on every later turn.
TURN_REASONS = ("urgent", "human_requested", "complaint", "change_request")
E164 = re.compile(r"\+[1-9][0-9]{6,14}")

# Twilio's message statuses, ranked: a callback never moves the status backwards.
# Terminal states share the top rank, so one terminal status is never replaced by another.
SMS_STATUS_RANK: dict[str, int] = {
    "accepted": 1,
    "queued": 2,
    "sending": 3,
    "sent": 4,
    "delivered": 5,
    "undelivered": 5,
    "failed": 5,
}
# A Twilio status that is a failure: its ErrorCode (a number) is kept as `sms_error`.
FAILED_SMS_STATUSES = ("undelivered", "failed")


class EscalationNotFoundError(AppError):
    status_code = 404
    code = "escalation_not_found"


class ContactPhoneRequiredError(AppError):
    status_code = 422
    code = "contact_phone_required"


class InvalidEscalationSettingsError(AppError):
    status_code = 422
    code = "invalid_escalation_settings"


@dataclass(frozen=True)
class EscalationRecord:
    id: uuid.UUID
    business_id: uuid.UUID
    interaction_id: uuid.UUID
    reason: str
    urgency: str
    status: str
    sms_status: str
    sms_error: str | None
    sms_sid: str | None
    created_at: datetime
    resolved_at: datetime | None
    notified_at: datetime | None = None


@dataclass(frozen=True)
class Recorded:
    record: EscalationRecord
    # A new row, or a resolved one reopened by this turn: an alert cycle starts.
    created: bool
    reopened: bool = False

    @property
    def fresh(self) -> bool:
        return self.created or self.reopened


@dataclass(frozen=True)
class EscalationSettings:
    contact_phone: str | None = None
    sms_enabled: bool = False
    clarification_threshold: int = DEFAULT_CLARIFICATION_THRESHOLD
    urgent_keywords: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EscalationSettingsView:
    settings: EscalationSettings
    customized: bool  # False: nothing saved yet, these are the defaults


class EscalationStore(Protocol):
    async def get_settings(self, business_id: uuid.UUID) -> EscalationSettings | None: ...
    async def upsert_settings(
        self, business_id: uuid.UUID, settings: EscalationSettings
    ) -> EscalationSettings: ...

    async def insert_or_get(
        self,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        reason: str,
        urgency: str,
        sms_status: str,
        reopen: bool = False,
    ) -> tuple[EscalationRecord, str]:
        """Insert-or-get on `(interaction_id, reason)` -> `(record, outcome)`, outcome
        `created | reopened | existing`. With `reopen`, a RESOLVED row is reopened
        (`open`, no `resolved_at`, a fresh alert cycle). A `pending` row is stored
        `skipped` instead when an earlier escalation of the interaction has an alert out
        (for high urgency: an earlier high-urgency one). Serialized per interaction."""
        ...

    async def get(
        self, business_id: uuid.UUID, escalation_id: uuid.UUID
    ) -> EscalationRecord | None: ...
    async def list(
        self,
        business_id: uuid.UUID,
        *,
        status: str | None,
        interaction_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[EscalationRecord]: ...
    async def resolve(
        self, business_id: uuid.UUID, escalation_id: uuid.UUID
    ) -> EscalationRecord | None: ...
    async def claim_pending(self, business_id: uuid.UUID, escalation_id: uuid.UUID) -> bool: ...
    async def finish_send(
        self,
        business_id: uuid.UUID,
        escalation_id: uuid.UUID,
        *,
        sms_status: str,
        sms_sid: str | None,
        sms_error: str | None,
        notified_at: datetime | None,
    ) -> EscalationRecord | None: ...
    async def get_by_sid(self, sms_sid: str) -> EscalationRecord | None:
        """Not tenant-scoped: the Twilio webhook has no tenant."""
        ...

    async def advance_sms_status(
        self, sms_sid: str, *, seen_status: str, sms_status: str, sms_error: str | None
    ) -> bool:
        """Conditional on `sms_status == seen_status`; False when someone changed it first."""
        ...

    async def checkpoint(self) -> None:
        """Commit what's written so far (called before a provider call)."""
        ...


class TurnEscalations(Protocol):
    """What the conversation engine needs from escalations (`EscalationService`)."""

    async def rules(self, tenant: Tenant) -> EscalationRules: ...
    async def record(
        self, tenant: Tenant, interaction_id: uuid.UUID, decision: EscalationDecision
    ) -> Recorded: ...
    async def notify(self, tenant: Tenant, recorded: Recorded) -> EscalationRecord: ...


def normalize_keywords(keywords: Sequence[str]) -> tuple[str, ...]:
    """Lower-cased, whitespace-collapsed, de-duplicated (order kept); bounds enforced."""
    cleaned: list[str] = []
    for raw in keywords:
        word = " ".join(raw.lower().split())
        if not 1 <= len(word) <= MAX_URGENT_KEYWORD_CHARS:
            raise InvalidEscalationSettingsError(
                f"Each urgent keyword must be 1 to {MAX_URGENT_KEYWORD_CHARS} characters."
            )
        if word not in cleaned:
            cleaned.append(word)
    if len(cleaned) > MAX_URGENT_KEYWORDS:
        raise InvalidEscalationSettingsError(
            f"At most {MAX_URGENT_KEYWORDS} urgent keywords are allowed."
        )
    return tuple(cleaned)


def validated_settings(settings: EscalationSettings) -> EscalationSettings:
    phone = (settings.contact_phone or "").strip() or None
    if phone is not None and not E164.fullmatch(phone):
        raise InvalidEscalationSettingsError("The contact phone must be in E.164 format.")
    if (
        not MIN_CLARIFICATION_THRESHOLD
        <= settings.clarification_threshold
        <= (MAX_CLARIFICATION_THRESHOLD)
    ):
        raise InvalidEscalationSettingsError(
            f"Clarification attempts must be {MIN_CLARIFICATION_THRESHOLD} to "
            f"{MAX_CLARIFICATION_THRESHOLD}."
        )
    if settings.sms_enabled and phone is None:
        raise ContactPhoneRequiredError("A contact phone is required to turn SMS alerts on.")
    return EscalationSettings(
        contact_phone=phone,
        sms_enabled=settings.sms_enabled,
        clarification_threshold=settings.clarification_threshold,
        urgent_keywords=normalize_keywords(settings.urgent_keywords),
    )


class EscalationService:
    def __init__(
        self,
        store: EscalationStore,
        notifier: Notifier,
        *,
        inbox_url: Callable[[uuid.UUID], str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._notifier = notifier
        self._inbox_url = inbox_url
        self._clock = clock

    # --- settings ---------------------------------------------------------------

    async def get_settings(self, tenant: Tenant) -> EscalationSettingsView:
        business = require_business(tenant)
        stored = await self._store.get_settings(business.id)
        if stored is None:
            return EscalationSettingsView(EscalationSettings(), customized=False)
        return EscalationSettingsView(stored, customized=True)

    async def save_settings(
        self, tenant: Tenant, settings: EscalationSettings
    ) -> EscalationSettingsView:
        business = require_business(tenant)
        saved = await self._store.upsert_settings(business.id, validated_settings(settings))
        return EscalationSettingsView(saved, customized=True)

    async def rules(self, tenant: Tenant) -> EscalationRules:
        """The owner's settings that change the policy's decision."""
        settings = (await self.get_settings(tenant)).settings
        return EscalationRules(
            clarification_threshold=settings.clarification_threshold,
            urgent_keywords=settings.urgent_keywords,
        )

    # --- recording and alerting -------------------------------------------------

    async def record(
        self, tenant: Tenant, interaction_id: uuid.UUID, decision: EscalationDecision
    ) -> Recorded:
        """Stores the escalation (insert-or-get). Does not commit and does not alert."""
        business = require_business(tenant)
        settings = (await self.get_settings(tenant)).settings
        alertable = settings.sms_enabled and settings.contact_phone is not None
        record, outcome = await self._store.insert_or_get(
            business.id,
            interaction_id,
            decision.reason,
            decision.urgency,
            "pending" if alertable else "disabled",
            decision.reason in TURN_REASONS,
        )
        created, reopened = outcome == "created", outcome == "reopened"
        if created or reopened:
            logger.info(
                "escalation reopened" if reopened else "escalation recorded",
                extra={
                    "business_id": str(business.id),
                    "interaction_id": str(interaction_id),
                    "escalation_id": str(record.id),
                    "reason": record.reason,
                    "sms_status": record.sms_status,
                },
            )
        return Recorded(record, created, reopened)

    async def notify(self, tenant: Tenant, recorded: Recorded) -> EscalationRecord:
        """Alerts the owner for a record this caller created and that is still `pending`.
        Never raises for a notifier problem: it becomes `failed` plus a fixed code."""
        record = recorded.record
        if not recorded.fresh or record.sms_status != "pending":
            return record
        business_id = require_business(tenant).id
        settings = (await self.get_settings(tenant)).settings
        if not await self._store.claim_pending(business_id, record.id):
            # Someone else is sending it (or it moved on): never a second SMS.
            return await self._store.get(business_id, record.id) or record
        if not settings.sms_enabled or settings.contact_phone is None:
            # Settings changed between recording and sending.
            updated = await self._store.finish_send(
                business_id,
                record.id,
                sms_status="disabled",
                sms_sid=None,
                sms_error=None,
                notified_at=None,
            )
            await self._store.checkpoint()
            return updated or record
        # The claim is durable before the provider call: no transaction across it.
        await self._store.checkpoint()
        alert = Alert(
            business_id=business_id,
            interaction_id=record.interaction_id,
            escalation_id=record.id,
            reason=record.reason,  # type: ignore[arg-type]
            to=settings.contact_phone,
            inbox_url=self._inbox_url(record.interaction_id),
        )
        status, sid, error, notified_at = "failed", None, None, None
        try:
            result = await self._notifier.send_alert(alert)
            status, sid, notified_at = result.status, result.provider_id, self._clock()
        except NotifierError as exc:
            error = exc.code
            logger.warning(
                "escalation alert not sent",
                extra={
                    "business_id": str(business_id),
                    "escalation_id": str(record.id),
                    "reason": record.reason,
                    "sms_error": exc.code,
                },
            )
        except Exception:
            # An SMS problem must never fail a chat turn; the cause is in the traceback.
            error = "provider_error"
            logger.warning(
                "escalation alert crashed",
                extra={
                    "business_id": str(business_id),
                    "escalation_id": str(record.id),
                    "reason": record.reason,
                    "sms_error": error,
                },
                exc_info=True,
            )
        finished = await self._store.finish_send(
            business_id,
            record.id,
            sms_status=status,
            sms_sid=sid,
            sms_error=error,
            notified_at=notified_at,
        )
        await self._store.checkpoint()
        return finished or record

    async def escalate(
        self, tenant: Tenant, interaction_id: uuid.UUID, decision: EscalationDecision
    ) -> EscalationRecord:
        """`record`, commit, then `notify`. N calls for one reason: 1 record, 1 alert."""
        recorded = await self.record(tenant, interaction_id, decision)
        await self._store.checkpoint()  # durable BEFORE any provider call
        return await self.notify(tenant, recorded)

    # --- owner actions ----------------------------------------------------------

    async def resolve(self, tenant: Tenant, escalation_id: uuid.UUID) -> EscalationRecord:
        business = require_business(tenant)
        record = await self._store.resolve(business.id, escalation_id)
        if record is None:
            raise EscalationNotFoundError("Escalation not found.")
        return record

    async def list(
        self,
        tenant: Tenant,
        status: StatusFilter = "open",
        interaction_id: uuid.UUID | None = None,
    ) -> Sequence[EscalationRecord]:
        business = require_business(tenant)
        return await self._store.list(
            business.id,
            status=None if status == "all" else status,
            interaction_id=interaction_id,
            limit=LIST_LIMIT,
        )

    # --- Twilio status callback -------------------------------------------------

    async def apply_sms_status(
        self, sms_sid: str, status: str, error_code: str | None = None
    ) -> bool:
        """Applies a delivery callback for message `sms_sid`. Monotonic: a repeat, an older
        status or an unknown status is a no-op, and so is an unknown sid. True if applied.
        The caller (the webhook route) commits."""
        new_status = status.strip().lower()
        new_rank = SMS_STATUS_RANK.get(new_status)
        if new_rank is None:
            return False
        for _ in range(3):  # a lost race re-reads and re-checks the rank
            record = await self._store.get_by_sid(sms_sid)
            if record is None:
                return False
            if SMS_STATUS_RANK.get(record.sms_status, 0) >= new_rank:
                return False
            error = record.sms_error
            if new_status in FAILED_SMS_STATUSES:
                code = (error_code or "").strip()
                error = code if code.isdigit() and len(code) <= 10 else error
            if await self._store.advance_sms_status(
                sms_sid, seen_status=record.sms_status, sms_status=new_status, sms_error=error
            ):
                return True
        return False
