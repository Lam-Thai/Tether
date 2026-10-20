"""The business-scoped query pattern every later domain repository must use.

This repo's repositories are plain functions (see `repositories/health.py`),
not classes, so "BusinessScopedRepository" here is a single helper rather
than a base class to subclass: a function from `(model, business_id)` to a
`SELECT` already filtered to that business. The first domain table with a
`business_id` column (FAQs, services, ...) builds its repository functions
on top of this instead of filtering by hand.

Repositories never see the client's request, so they can't themselves tell
a trusted `business_id` apart from a forged one — that's `TenantContext`'s
job (`core/tenant.py`), one layer up. The hard rule this enforces is on the
*caller*: every domain repository function must take a `business_id`
resolved from `Tenant`, never one read straight off the request.

`businesses` itself doesn't use this — it's the tenant root, scoped by
`owner_id` instead (see `repositories/business.py`).
"""

import uuid
from typing import Protocol

from sqlalchemy import Select, select


class HasBusinessId(Protocol):
    business_id: object


def business_scoped_select[ModelT: HasBusinessId](
    model: type[ModelT], business_id: uuid.UUID
) -> Select:
    return select(model).where(model.business_id == business_id)  # type: ignore[arg-type]
