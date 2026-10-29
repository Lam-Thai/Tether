"""`DELETE /api/v1/account`: self-serve account deletion (issue #50). Router only: parse, call
one service method, shape the response. The service commits for itself (it must, before it
calls Clerk), so there is no commit here.

Depends on `get_tenant`, not `get_entitled_tenant`: an owner with no plan, or no business yet,
can still delete their account. The user and the business come from the token, never from
the request.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from core.account import get_account_eraser
from core.tenant import Tenant, get_tenant
from schemas.account import AccountDelete, AccountDeletionRead
from services.account import AccountEraser

router = APIRouter(tags=["account"])

CurrentTenant = Annotated[Tenant, Depends(get_tenant)]
Eraser = Annotated[AccountEraser, Depends(get_account_eraser)]


@router.delete("/account", response_model=AccountDeletionRead)
async def delete_account(
    body: AccountDelete, tenant: CurrentTenant, eraser: Eraser
) -> AccountDeletionRead:
    report = await eraser.erase(tenant, body.confirmation)
    return AccountDeletionRead.model_validate(report)
