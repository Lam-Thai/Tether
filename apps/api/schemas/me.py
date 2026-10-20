from uuid import UUID

from pydantic import BaseModel

from schemas.billing import SubscriptionRead
from schemas.business import BusinessRead


class MeRead(BaseModel):
    id: UUID
    email: str
    business: BusinessRead | None
    # None until the owner has a business; otherwise always present.
    subscription: SubscriptionRead | None
