"""In-memory `PhoneNumberStore` with the same contract as `SqlPhoneNumberStore`: UNIQUE
`phone_number` and `provider_number_sid` across every business, scoped reads."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from models.phone_number import PhoneNumber


class MemoryPhoneNumberStore:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, PhoneNumber] = {}
        self.checkpoints = 0

    async def list_for_business(self, business_id: uuid.UUID) -> Sequence[PhoneNumber]:
        return sorted(
            (r for r in self.rows.values() if r.business_id == business_id),
            key=lambda r: (r.created_at, r.id),
        )

    async def get(self, business_id: uuid.UUID, number_id: uuid.UUID) -> PhoneNumber | None:
        row = self.rows.get(number_id)
        return row if row is not None and row.business_id == business_id else None

    async def get_by_sid(
        self, business_id: uuid.UUID, provider_number_sid: str
    ) -> PhoneNumber | None:
        for row in self.rows.values():
            if row.business_id == business_id and row.provider_number_sid == provider_number_sid:
                return row
        return None

    async def associated_sids(self, sids: Sequence[str]) -> set[str]:
        return {r.provider_number_sid for r in self.rows.values() if r.provider_number_sid in sids}

    async def insert(
        self,
        *,
        business_id: uuid.UUID,
        phone_number: str,
        provider_number_sid: str,
        status: str,
        last_error: str | None,
        verified_at: datetime | None,
    ) -> PhoneNumber | None:
        for row in self.rows.values():
            if row.phone_number == phone_number or row.provider_number_sid == provider_number_sid:
                return None
        now = datetime.now(UTC)
        row = PhoneNumber(
            id=uuid.uuid4(),
            business_id=business_id,
            provider="twilio",
            phone_number=phone_number,
            provider_number_sid=provider_number_sid,
            status=status,
            last_error=last_error,
            verified_at=verified_at,
            created_at=now,
            updated_at=now,
        )
        self.rows[row.id] = row
        return row

    async def save(self, number: PhoneNumber) -> PhoneNumber:
        self.rows[number.id] = number
        return number

    async def delete(self, business_id: uuid.UUID, number_id: uuid.UUID) -> bool:
        if await self.get(business_id, number_id) is None:
            return False
        del self.rows[number_id]
        return True

    async def checkpoint(self) -> None:
        self.checkpoints += 1
