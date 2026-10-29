"""`/api/v1/faqs`: the owner's FAQ list. Router only: parse, call one service
function, commit, shape. Every route needs an entitled tenant; the business
always comes from the tenant, never from the request."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.db import get_session
from core.faqs import get_faq_max, get_faq_store
from core.tenant import Tenant
from repositories.faqs import SqlFaqStore
from schemas.faq import FaqCreate, FaqList, FaqRead, FaqUpdate
from services import faqs as faq_service

router = APIRouter(tags=["faqs"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
FaqStore = Annotated[SqlFaqStore, Depends(get_faq_store)]
MaxFaqs = Annotated[int, Depends(get_faq_max)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/faqs", response_model=FaqList)
async def list_faqs(tenant: Entitled, store: FaqStore, max_faqs: MaxFaqs) -> FaqList:
    faqs = await faq_service.list_faqs(store, tenant)
    return FaqList(faqs=[FaqRead.model_validate(f) for f in faqs], max_faqs=max_faqs)


@router.post("/faqs", response_model=FaqRead, status_code=status.HTTP_201_CREATED)
async def create_faq(
    body: FaqCreate, tenant: Entitled, store: FaqStore, max_faqs: MaxFaqs, session: Session
) -> FaqRead:
    faq = await faq_service.create_faq(store, tenant, max_faqs=max_faqs, **body.model_dump())
    await session.commit()
    return FaqRead.model_validate(faq)


@router.patch("/faqs/{faq_id}", response_model=FaqRead)
async def patch_faq(
    faq_id: UUID, body: FaqUpdate, tenant: Entitled, store: FaqStore, session: Session
) -> FaqRead:
    faq = await faq_service.update_faq(store, tenant, faq_id, **body.model_dump(exclude_unset=True))
    await session.commit()
    return FaqRead.model_validate(faq)


@router.delete("/faqs/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq(faq_id: UUID, tenant: Entitled, store: FaqStore, session: Session) -> Response:
    await faq_service.delete_faq(store, tenant, faq_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
