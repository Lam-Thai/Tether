"""Unit tests for the business service — fake repository callables, no DB."""

import uuid

import pytest

from core.tenant import Tenant
from models.business import Business
from models.user import User
from services.business import (
    BusinessAlreadyExistsError,
    BusinessNotFoundError,
    create_business,
    get_current_business,
    update_business,
)


def _user() -> User:
    return User(id=uuid.uuid4(), external_auth_id="user_123", email="owner@example.com")


def _business(owner_id: uuid.UUID) -> Business:
    return Business(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Alpha Salon",
        industry="Salon",
        address="123 Main St",
        timezone="America/New_York",
        phone=None,
        contact_email=None,
    )


async def test_get_current_business_returns_the_tenants_business():
    user = _user()
    business = _business(user.id)
    tenant = Tenant(user=user, business=business)

    result = await get_current_business(tenant)

    assert result is business


async def test_get_current_business_raises_not_found_before_onboarding():
    tenant = Tenant(user=_user(), business=None)

    with pytest.raises(BusinessNotFoundError):
        await get_current_business(tenant)


async def test_create_business_calls_repo_with_owner_id_from_tenant():
    user = _user()
    tenant = Tenant(user=user, business=None)
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _business(user.id)

    await create_business(
        fake_create,
        tenant,
        name="Alpha Salon",
        industry="Salon",
        address="123 Main St",
        timezone="America/New_York",
        phone=None,
        contact_email=None,
    )

    assert captured["owner_id"] == user.id


async def test_create_business_rejects_a_tenant_that_already_has_one():
    user = _user()
    tenant = Tenant(user=user, business=_business(user.id))

    async def fake_create(**kwargs):
        raise AssertionError("should not be called")

    with pytest.raises(BusinessAlreadyExistsError):
        await create_business(
            fake_create,
            tenant,
            name="Alpha Salon",
            industry="Salon",
            address="123 Main St",
            timezone="America/New_York",
            phone=None,
            contact_email=None,
        )


async def test_update_business_rejects_a_business_the_tenant_does_not_own():
    tenant = Tenant(user=_user(), business=None)

    async def fake_get_by_id_for_owner(business_id, owner_id):
        return None  # forged/cross-tenant business_id

    async def fake_update(business, **fields):
        raise AssertionError("should not be called")

    with pytest.raises(BusinessNotFoundError):
        await update_business(
            fake_get_by_id_for_owner, fake_update, tenant, uuid.uuid4(), name="New Name"
        )


async def test_update_business_applies_fields_to_the_owned_business():
    user = _user()
    tenant = Tenant(user=user, business=None)
    business = _business(user.id)

    async def fake_get_by_id_for_owner(business_id, owner_id):
        return business

    async def fake_update(target, **fields):
        for key, value in fields.items():
            setattr(target, key, value)
        return target

    result = await update_business(
        fake_get_by_id_for_owner, fake_update, tenant, business.id, name="New Name"
    )

    assert result.name == "New Name"
