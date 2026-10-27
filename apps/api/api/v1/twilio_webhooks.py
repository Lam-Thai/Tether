"""`/api/v1/webhooks/twilio/*` (Twilio-facing). Router only.

`TwilioWebhookAuth` validates `X-Twilio-Signature` before the payload is used for
anything (403 `invalid_signature`, also when Twilio isn't configured). There is no
tenant: the business is resolved from the stored escalation row by message sid, never
from the payload. Twilio retries callbacks and may deliver them out of order, so the
status update is monotonic (a repeat or an older status is a no-op) and the route
always answers 204 after a valid signature, unknown sid included.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session
from core.escalation import build_escalation_service
from core.twilio import SMS_STATUS_PATH, TwilioWebhookAuth
from services.notifier import LogNotifier

router = APIRouter(tags=["webhooks"])

twilio_auth = TwilioWebhookAuth()

SignedForm = Annotated[dict[str, str], Depends(twilio_auth)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post(SMS_STATUS_PATH.removeprefix("/api/v1"), status_code=204, include_in_schema=False)
async def post_sms_status(fields: SignedForm, session: Session) -> Response:
    # A status callback never sends anything, so no real notifier is needed.
    escalations = build_escalation_service(session, LogNotifier())
    await escalations.apply_sms_status(
        fields.get("MessageSid", ""),
        fields.get("MessageStatus", ""),
        fields.get("ErrorCode") or None,
    )
    await session.commit()
    return Response(status_code=204)
