"""Service catalog CRUD against an in-memory store (no DB)."""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.errors import ServiceNameTakenError
from core.tenant import Tenant
from models.service import Service
from services import catalog as svc
from services.business import BusinessNotFoundError


class MemoryCatalogStore:
    def __init__(self) -> None:
        self.rows: list[Service] = []

    async def list_services(self, business_id, *, active_only):
        rows = [r for r in self.rows if r.business_id == business_id]
        if active_only:
            rows = [r for r in rows if r.active]
        return sorted(rows, key=lambda r: r.name.lower())

    async def get_service(self, business_id, service_id):
        return next(
            (r for r in self.rows if r.business_id == business_id and r.id == service_id), None
        )

    async def lock_and_count_services(self, business_id):
        return len(await self.list_services(business_id, active_only=False))

    def _check_name(self, business_id, name, ignore=None):
        for r in self.rows:
            if r is not ignore and r.business_id == business_id and r.name.lower() == name.lower():
                raise ServiceNameTakenError("taken")

    async def create_service(
        self, business_id, *, name, description, duration_minutes, price, active
    ):
        self._check_name(business_id, name)
        row = Service(
            id=uuid.uuid4(),
            business_id=business_id,
            name=name,
            description=description,
            duration_minutes=duration_minutes,
            price=price,
            active=active,
        )
        self.rows.append(row)
        return row

    async def save_service(self, service):
        self._check_name(service.business_id, service.name, ignore=service)
        return service

    async def delete_service(self, service):
        self.rows.remove(service)


def _tenant(currency: str = "USD") -> Tenant:
    business = SimpleNamespace(id=uuid.uuid4(), currency=currency)
    return Tenant(user=SimpleNamespace(), business=business)  # type: ignore[arg-type]


async def test_create_list_update_disable_and_delete():
    store, tenant = MemoryCatalogStore(), _tenant("EUR")

    cut = await svc.create_service(
        store, tenant, max_services=5, name="Haircut", duration_minutes=45, price=Decimal("40.00")
    )
    await svc.create_service(
        store, tenant, max_services=5, name="beard trim", duration_minutes=15, active=False
    )
    assert (cut.description, cut.active) == (None, True)
    await svc.update_service(store, tenant, cut.id, price=None, description="Wash included")
    await svc.update_service(store, tenant, cut.id, active=False)

    catalog = await svc.get_catalog(store, tenant, max_services=5)
    assert [s.name for s in catalog.services] == ["beard trim", "Haircut"]  # inactive included
    assert (catalog.currency, catalog.max_services) == ("EUR", 5)
    assert (cut.price, cut.description, cut.active) == (None, "Wash included", False)
    await svc.delete_service(store, tenant, cut.id)
    assert [s.name for s in await svc.list_services(store, tenant)] == ["beard trim"]


async def test_cap_and_duplicate_names_are_conflicts_per_business():
    store, tenant, other = MemoryCatalogStore(), _tenant(), _tenant()
    first = await svc.create_service(store, tenant, max_services=2, name="Cut", duration_minutes=30)
    await svc.create_service(store, tenant, max_services=2, name="Colour", duration_minutes=90)

    with pytest.raises(svc.ServiceLimitReachedError) as limit:
        await svc.create_service(store, tenant, max_services=2, name="Perm", duration_minutes=90)
    assert limit.value.status_code == 409 and limit.value.code == "service_limit_reached"
    with pytest.raises(ServiceNameTakenError) as taken:
        await svc.update_service(store, tenant, first.id, name="COLOUR")
    assert taken.value.status_code == 409 and taken.value.code == "service_name_taken"
    await svc.create_service(store, other, max_services=2, name="Cut", duration_minutes=30)


async def test_unknown_and_foreign_ids_are_not_found_and_untouched():
    store, mine, theirs = MemoryCatalogStore(), _tenant(), _tenant()
    foreign = await svc.create_service(
        store, theirs, max_services=5, name="Cut", duration_minutes=30
    )

    for call in (
        svc.update_service(store, mine, foreign.id, name="hacked"),
        svc.update_service(store, mine, uuid.uuid4(), name="x"),
        svc.delete_service(store, mine, foreign.id),
    ):
        with pytest.raises(svc.ServiceNotFoundError) as raised:
            await call
        assert raised.value.status_code == 404 and raised.value.code == "service_not_found"
    assert foreign.name == "Cut" and foreign in store.rows


async def test_unknown_fields_and_missing_business_are_rejected():
    store = MemoryCatalogStore()
    tenant = _tenant()
    created = await svc.create_service(
        store, tenant, max_services=5, name="Cut", duration_minutes=30
    )
    with pytest.raises(ValueError):
        await svc.update_service(store, tenant, created.id, business_id=uuid.uuid4())
    with pytest.raises(BusinessNotFoundError):
        await svc.list_services(store, Tenant(user=SimpleNamespace(), business=None))  # type: ignore[arg-type]
