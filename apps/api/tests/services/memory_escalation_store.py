"""In-memory `EscalationStore`, same contract as `core.escalation.SqlEscalationStore`."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime

from repositories.escalations import ALERT_OUT_STATUSES
from services.escalation import EscalationRecord, EscalationSettings


class MemoryEscalationStore:
    def __init__(self) -> None:
        self.settings: dict[uuid.UUID, EscalationSettings] = {}
        self.rows: dict[uuid.UUID, EscalationRecord] = {}
        self.checkpoints = 0

    async def get_settings(self, business_id):
        return self.settings.get(business_id)

    async def upsert_settings(self, business_id, settings):
        self.settings[business_id] = settings
        return settings

    async def insert_or_get(
        self, business_id, interaction_id, reason, urgency, sms_status, reopen=False
    ):
        mine = [
            r
            for r in self.rows.values()
            if r.business_id == business_id and r.interaction_id == interaction_id
        ]
        earlier = [
            r
            for r in mine
            if r.reason != reason
            and r.sms_status in ALERT_OUT_STATUSES
            and (urgency != "high" or r.urgency == "high")
        ]
        initial = "skipped" if earlier and sms_status == "pending" else sms_status
        for row in mine:
            if row.reason == reason:
                if reopen and row.status == "resolved":
                    row = replace(
                        row,
                        status="open",
                        resolved_at=None,
                        urgency=urgency,
                        sms_status=initial,
                        sms_sid=None,
                        sms_error=None,
                        notified_at=None,
                    )
                    self.rows[row.id] = row
                    return row, "reopened"
                return row, "existing"
        record = EscalationRecord(
            id=uuid.uuid4(),
            business_id=business_id,
            interaction_id=interaction_id,
            reason=reason,
            urgency=urgency,
            status="open",
            sms_status=initial,
            sms_error=None,
            sms_sid=None,
            created_at=datetime.now(UTC),
            resolved_at=None,
        )
        self.rows[record.id] = record
        return record, "created"

    def _mine(self, business_id, escalation_id):
        row = self.rows.get(escalation_id)
        return row if row is not None and row.business_id == business_id else None

    async def get(self, business_id, escalation_id):
        return self._mine(business_id, escalation_id)

    async def list(self, business_id, *, status, interaction_id, limit):
        rows = [
            r
            for r in self.rows.values()
            if r.business_id == business_id
            and (status is None or r.status == status)
            and (interaction_id is None or r.interaction_id == interaction_id)
        ]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)[:limit]

    async def resolve(self, business_id, escalation_id):
        row = self._mine(business_id, escalation_id)
        if row is None:
            return None
        if row.status == "open":
            row = replace(row, status="resolved", resolved_at=datetime.now(UTC))
            self.rows[row.id] = row
        return row

    async def claim_pending(self, business_id, escalation_id):
        row = self._mine(business_id, escalation_id)
        if row is None or row.sms_status != "pending":
            return False
        self.rows[row.id] = replace(row, sms_status="sending")
        return True

    async def finish_send(
        self, business_id, escalation_id, *, sms_status, sms_sid, sms_error, notified_at
    ):
        row = self._mine(business_id, escalation_id)
        if row is None or row.sms_status != "sending":
            return None
        row = replace(
            row,
            sms_status=sms_status,
            sms_sid=sms_sid,
            sms_error=sms_error,
            notified_at=notified_at,
        )
        self.rows[row.id] = row
        return row

    async def get_by_sid(self, sms_sid):
        return next((r for r in self.rows.values() if r.sms_sid == sms_sid), None)

    async def advance_sms_status(self, sms_sid, *, seen_status, sms_status, sms_error):
        row = await self.get_by_sid(sms_sid)
        if row is None or row.sms_status != seen_status:
            return False
        self.rows[row.id] = replace(row, sms_status=sms_status, sms_error=sms_error)
        return True

    async def checkpoint(self):
        self.checkpoints += 1
