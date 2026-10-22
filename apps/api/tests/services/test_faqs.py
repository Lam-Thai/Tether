"""FAQ CRUD service against an in-memory store (no DB)."""

import uuid
from types import SimpleNamespace

import pytest

from core.tenant import Tenant
from models.faq import Faq
from services import faqs as svc
from services.business import BusinessNotFoundError


class MemoryFaqStore:
    def __init__(self) -> None:
        self.rows: list[Faq] = []

    async def list_faqs(self, business_id):
        return [r for r in self.rows if r.business_id == business_id]

    async def get_faq(self, business_id, faq_id):
        return next((r for r in self.rows if r.business_id == business_id and r.id == faq_id), None)

    async def lock_and_count_faqs(self, business_id):
        return len(await self.list_faqs(business_id))

    async def create_faq(self, business_id, *, question, answer, category, active):
        row = Faq(
            id=uuid.uuid4(),
            business_id=business_id,
            question=question,
            answer=answer,
            category=category,
            active=active,
        )
        self.rows.append(row)
        return row

    async def save_faq(self, faq):
        return faq

    async def delete_faq(self, faq):
        self.rows.remove(faq)


def _tenant() -> Tenant:
    return Tenant(user=SimpleNamespace(), business=SimpleNamespace(id=uuid.uuid4()))  # type: ignore[arg-type]


async def test_create_list_update_disable_and_delete():
    store, tenant = MemoryFaqStore(), _tenant()

    created = await svc.create_faq(store, tenant, max_faqs=5, question="Q?", answer="A.")
    assert (created.category, created.active) == (None, True)
    await svc.update_faq(store, tenant, created.id, category="Booking", answer="B.")
    await svc.update_faq(store, tenant, created.id, active=False)
    await svc.update_faq(store, tenant, created.id, category=None)

    (only,) = await svc.list_faqs(store, tenant)
    assert (only.question, only.answer, only.category, only.active) == ("Q?", "B.", None, False)
    await svc.delete_faq(store, tenant, created.id)
    assert await svc.list_faqs(store, tenant) == []


async def test_cap_is_enforced_per_business():
    store, tenant, other = MemoryFaqStore(), _tenant(), _tenant()
    for i in range(2):
        await svc.create_faq(store, tenant, max_faqs=2, question=f"Q{i}", answer="A")

    with pytest.raises(svc.FaqLimitReachedError) as raised:
        await svc.create_faq(store, tenant, max_faqs=2, question="Q3", answer="A")
    assert raised.value.status_code == 409 and raised.value.code == "faq_limit_reached"
    await svc.create_faq(store, other, max_faqs=2, question="Q", answer="A")  # not shared


async def test_unknown_and_foreign_ids_are_not_found_and_untouched():
    store, mine, theirs = MemoryFaqStore(), _tenant(), _tenant()
    foreign = await svc.create_faq(store, theirs, max_faqs=5, question="Q", answer="A")

    for call in (
        svc.update_faq(store, mine, foreign.id, answer="hacked"),
        svc.update_faq(store, mine, uuid.uuid4(), answer="x"),
        svc.delete_faq(store, mine, foreign.id),
    ):
        with pytest.raises(svc.FaqNotFoundError) as raised:
            await call
        assert raised.value.status_code == 404 and raised.value.code == "faq_not_found"
    assert foreign.answer == "A" and foreign in store.rows


async def test_unknown_fields_and_missing_business_are_rejected():
    store = MemoryFaqStore()
    created = await svc.create_faq(store, _tenant(), max_faqs=5, question="Q", answer="A")
    tenant = _tenant()
    with pytest.raises(ValueError):
        await svc.update_faq(store, tenant, created.id, business_id=uuid.uuid4())
    with pytest.raises(BusinessNotFoundError):
        await svc.list_faqs(store, Tenant(user=SimpleNamespace(), business=None))  # type: ignore[arg-type]
