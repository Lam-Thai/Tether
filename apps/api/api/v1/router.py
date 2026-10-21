"""Root `/api/v1` router."""

from fastapi import APIRouter

from api.v1.agent import router as agent_router
from api.v1.billing import router as billing_router
from api.v1.business import router as business_router
from api.v1.me import router as me_router
from api.v1.usage import router as usage_router

router = APIRouter(prefix="/api/v1")
router.include_router(me_router)
router.include_router(business_router)
router.include_router(billing_router)
router.include_router(agent_router)
router.include_router(usage_router)
