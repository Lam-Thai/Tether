"""Owner CRUD for the service catalog (PRD §8.5). Disabling is `update_service(active=False)`.

An unknown id and another tenant's id are indistinguishable (404). The cap is
passed in by the caller (it comes from settings); the store serializes creates
per business so concurrent requests cannot exceed it. A duplicate name (ignoring
case) is a 409 raised by the store. No FastAPI or SQLAlchemy imports.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from core.errors import AppError
from core.tenant import Tenant
from models.service import Service
from services.agent import require_business

EDITABLE_FIELDS = frozenset({"name", "description", "duration_minutes", "price", "active"})


class ServiceNotFoundError(AppError):
    status_code = 404
    code = "service_not_found"


class ServiceLimitReachedError(AppError):
    status_code = 409
    code = "service_limit_reached"


class CatalogStore(Protocol):
    async def list_services(
        self, business_id: uuid.UUID, *, active_only: bool
    ) -> Sequence[Service]: ...
    async def get_service(
        self, business_id: uuid.UUID, service_id: uuid.UUID
    ) -> Service | None: ...
    async def lock_and_count_services(self, business_id: uuid.UUID) -> int:
        """Serialize creates for this business, then count all its services."""
        ...

    async def create_service(
        self,
        business_id: uuid.UUID,
        *,
        name: str,
        description: str | None,
        duration_minutes: int,
        price: Decimal | None,
        active: bool,
    ) -> Service:
        """Raises `ServiceNameTakenError` when the name exists (ignoring case)."""
        ...

    async def save_service(self, service: Service) -> Service:
        """Raises `ServiceNameTakenError` when a rename collides."""
        ...

    async def delete_service(self, service: Service) -> None: ...


async def list_services(store: CatalogStore, tenant: Tenant) -> Sequence[Service]:
    """Every service of the owner's business, inactive ones included."""
    return await store.list_services(require_business(tenant).id, active_only=False)


@dataclass(frozen=True)
class Catalog:
    services: Sequence[Service]
    # ISO 4217 code every price is in (the business's currency).
    currency: str
    max_services: int


async def get_catalog(store: CatalogStore, tenant: Tenant, *, max_services: int) -> Catalog:
    business = require_business(tenant)
    services = await store.list_services(business.id, active_only=False)
    return Catalog(services=services, currency=business.currency, max_services=max_services)


async def create_service(
    store: CatalogStore,
    tenant: Tenant,
    *,
    max_services: int,
    name: str,
    duration_minutes: int,
    description: str | None = None,
    price: Decimal | None = None,
    active: bool = True,
) -> Service:
    business = require_business(tenant)
    if await store.lock_and_count_services(business.id) >= max_services:
        raise ServiceLimitReachedError(
            f"You can keep at most {max_services} services. Delete one to add more."
        )
    return await store.create_service(
        business.id,
        name=name,
        description=description,
        duration_minutes=duration_minutes,
        price=price,
        active=active,
    )


async def _require_service(store: CatalogStore, tenant: Tenant, service_id: uuid.UUID) -> Service:
    service = await store.get_service(require_business(tenant).id, service_id)
    if service is None:
        raise ServiceNotFoundError("Service not found.")
    return service


async def update_service(
    store: CatalogStore, tenant: Tenant, service_id: uuid.UUID, **changes: Any
) -> Service:
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")
    service = await _require_service(store, tenant, service_id)
    for key, value in changes.items():
        setattr(service, key, value)
    return await store.save_service(service)


async def delete_service(store: CatalogStore, tenant: Tenant, service_id: uuid.UUID) -> None:
    await store.delete_service(await _require_service(store, tenant, service_id))
