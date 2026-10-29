"""`/api/v1/dashboard/summary`, `/integrations/status`, `DELETE /business/test-data`.
Router only: parse, call one service method, commit what it wrote, shape the response.

Every route depends on `get_entitled_tenant` (402 without a plan); the business always
comes from the tenant, never from the request.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.db import get_session
from core.readiness import get_dashboard_metrics, get_integration_status, get_test_data_eraser
from core.tenant import Tenant
from schemas.dashboard import (
    AgentStatusRead,
    CalendarIntegrationRead,
    DashboardSummaryRead,
    ErasureRead,
    IntegrationErrorRead,
    IntegrationStatusRead,
    PhoneIntegrationRead,
    ReadinessRead,
    TestDataDelete,
)
from schemas.usage import UsageRead
from services.dashboard import DashboardMetrics
from services.integration_status import IntegrationStatus
from services.test_data import TestDataEraser

router = APIRouter(tags=["dashboard"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Metrics = Annotated[DashboardMetrics, Depends(get_dashboard_metrics)]
Integrations = Annotated[IntegrationStatus, Depends(get_integration_status)]
Eraser = Annotated[TestDataEraser, Depends(get_test_data_eraser)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/dashboard/summary", response_model=DashboardSummaryRead, response_model_by_alias=True)
async def get_summary(
    tenant: Entitled,
    metrics: Metrics,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> DashboardSummaryRead:
    result = await metrics.summary(tenant, from_date, to_date)
    return DashboardSummaryRead(
        from_=result.range.start,
        to=result.range.end,
        timezone=result.timezone,
        total_interactions=result.total_interactions,
        successful_bookings=result.successful_bookings,
        faqs_answered=result.faqs_answered,
        open_escalations=result.open_escalations,
    )


@router.get("/integrations/status", response_model=IntegrationStatusRead)
async def get_integrations_status(
    tenant: Entitled, integrations: Integrations, session: Session
) -> IntegrationStatusRead:
    overview = await integrations.read(tenant)
    await session.commit()  # the default agent row, a calendar probe's result
    readiness = ReadinessRead.from_activation(overview.activation)
    return IntegrationStatusRead(
        calendar=CalendarIntegrationRead.model_validate(overview.calendar),
        phone=PhoneIntegrationRead.model_validate(overview.phone),
        agent=AgentStatusRead(active=readiness.active, ready=readiness.ready),
        readiness=readiness,
        errors=[IntegrationErrorRead.model_validate(e) for e in overview.errors],
        usage=UsageRead.model_validate(overview.usage),
    )


@router.delete("/business/test-data", response_model=ErasureRead)
async def delete_test_data(
    body: TestDataDelete, tenant: Entitled, eraser: Eraser, session: Session
) -> ErasureRead:
    report = await eraser.erase(tenant, body.confirmation)
    await session.commit()
    return ErasureRead.model_validate(report)
