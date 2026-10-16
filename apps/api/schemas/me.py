from uuid import UUID

from pydantic import BaseModel

from schemas.business import BusinessRead


class MeRead(BaseModel):
    id: UUID
    email: str
    business: BusinessRead | None
