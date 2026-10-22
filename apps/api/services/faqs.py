"""Owner CRUD for FAQs (PRD §8.4). Disabling is `update_faq(active=False)`.

An unknown id and another tenant's id are indistinguishable (404). The cap is
passed in by the caller (it comes from settings); the store serializes creates
per business so concurrent requests cannot exceed it. No FastAPI or SQLAlchemy
imports.
"""

import uuid
from collections.abc import Sequence
from typing import Any, Protocol

from core.errors import AppError
from core.tenant import Tenant
from models.faq import Faq
from services.agent import require_business

EDITABLE_FIELDS = frozenset({"question", "answer", "category", "active"})


class FaqNotFoundError(AppError):
    status_code = 404
    code = "faq_not_found"


class FaqLimitReachedError(AppError):
    status_code = 409
    code = "faq_limit_reached"


class FaqStore(Protocol):
    async def list_faqs(self, business_id: uuid.UUID) -> Sequence[Faq]: ...
    async def get_faq(self, business_id: uuid.UUID, faq_id: uuid.UUID) -> Faq | None: ...
    async def lock_and_count_faqs(self, business_id: uuid.UUID) -> int:
        """Serialize creates for this business, then count all its FAQs."""
        ...

    async def create_faq(
        self,
        business_id: uuid.UUID,
        *,
        question: str,
        answer: str,
        category: str | None,
        active: bool,
    ) -> Faq: ...
    async def save_faq(self, faq: Faq) -> Faq: ...
    async def delete_faq(self, faq: Faq) -> None: ...


async def list_faqs(store: FaqStore, tenant: Tenant) -> Sequence[Faq]:
    return await store.list_faqs(require_business(tenant).id)


async def create_faq(
    store: FaqStore,
    tenant: Tenant,
    *,
    max_faqs: int,
    question: str,
    answer: str,
    category: str | None = None,
    active: bool = True,
) -> Faq:
    business = require_business(tenant)
    if await store.lock_and_count_faqs(business.id) >= max_faqs:
        raise FaqLimitReachedError(f"You can keep at most {max_faqs} FAQs. Delete one to add more.")
    return await store.create_faq(
        business.id, question=question, answer=answer, category=category, active=active
    )


async def _require_faq(store: FaqStore, tenant: Tenant, faq_id: uuid.UUID) -> Faq:
    faq = await store.get_faq(require_business(tenant).id, faq_id)
    if faq is None:
        raise FaqNotFoundError("FAQ not found.")
    return faq


async def update_faq(store: FaqStore, tenant: Tenant, faq_id: uuid.UUID, **changes: Any) -> Faq:
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")
    faq = await _require_faq(store, tenant, faq_id)
    for key, value in changes.items():
        setattr(faq, key, value)
    return await store.save_faq(faq)


async def delete_faq(store: FaqStore, tenant: Tenant, faq_id: uuid.UUID) -> None:
    await store.delete_faq(await _require_faq(store, tenant, faq_id))
