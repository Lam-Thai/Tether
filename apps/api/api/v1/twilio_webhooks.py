"""`/api/v1/webhooks/twilio/*` (Twilio-facing). Router only.

`TwilioWebhookAuth` validates `X-Twilio-Signature` before the payload is used for
anything (403 `invalid_signature`, also when Twilio isn't configured). There is no
tenant: the business is resolved from a stored row (the escalation by message sid, the
phone number called, the interaction by CallSid), never from the payload.

- `sms-status`: Twilio retries callbacks and may deliver them out of order, so the
  status update is monotonic (a repeat or an older status is a no-op) and the route
  always answers 204 after a valid signature, unknown sid included.
- `voice`, `input`, `input/wait`: answer TwiML from `CallSessionService` +
  `TwimlRenderer`. The turn number, silence count and hold attempt ride in the query
  string of URLs Tether rendered (and Twilio signed). An unexpected error still answers
  TwiML (an apology and a hang-up) so the caller never hears Twilio's error message.
- `status`: a terminal call status ends the interaction; always 204.
"""

import logging
from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session
from core.escalation import build_escalation_service
from core.twilio import SMS_STATUS_PATH, TwilioWebhookAuth
from core.voice import (
    INPUT_PATH,
    STATUS_PATH,
    VOICE_PATH,
    WAIT_PATH,
    get_call_session,
    get_twiml_renderer,
)
from services.call_session import CallSessionService, InboundCall
from services.notifier import LogNotifier
from services.twiml import CallStep, Close, TwimlRenderer

logger = logging.getLogger("tether.voice")

router = APIRouter(tags=["webhooks"])

twilio_auth = TwilioWebhookAuth()

SignedForm = Annotated[dict[str, str], Depends(twilio_auth)]
Session = Annotated[AsyncSession, Depends(get_session)]
Calls = Annotated[CallSessionService, Depends(get_call_session)]
Renderer = Annotated[TwimlRenderer, Depends(get_twiml_renderer)]
Turn = Annotated[int, Query(ge=1, le=1000)]

ERROR_LINE = "Sorry, something went wrong on our end. Please call back in a moment. Goodbye."


def _path(full: str) -> str:
    return full.removeprefix("/api/v1")


@router.post(_path(SMS_STATUS_PATH), status_code=204, include_in_schema=False)
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


async def _twiml(renderer: TwimlRenderer, step: Awaitable[CallStep]) -> Response:
    try:
        decided = await step
    except Exception as exc:
        # The type only: a database error's message quotes bound parameters (caller
        # number, speech), like `services/tools.py` and `services/lifecycle.py`.
        logger.error("voice webhook failed", extra={"error_type": type(exc).__name__})
        decided = Close(ERROR_LINE)
    return Response(content=renderer.render(decided), media_type="application/xml")


@router.post(_path(VOICE_PATH), include_in_schema=False)
async def post_voice(fields: SignedForm, calls: Calls, renderer: Renderer) -> Response:
    call = InboundCall(
        call_sid=fields.get("CallSid", ""),
        to=fields.get("To", ""),
        caller=fields.get("From") or None,
    )
    return await _twiml(renderer, calls.start(call))


@router.post(_path(INPUT_PATH), include_in_schema=False)
async def post_input(
    fields: SignedForm,
    calls: Calls,
    renderer: Renderer,
    turn: Turn,
    silences: Annotated[int, Query(ge=0, le=10)] = 0,
) -> Response:
    return await _twiml(
        renderer,
        calls.respond(fields.get("CallSid", ""), turn, silences, fields.get("SpeechResult")),
    )


@router.post(_path(WAIT_PATH), include_in_schema=False)
async def post_wait(
    fields: SignedForm,
    calls: Calls,
    renderer: Renderer,
    turn: Turn,
    attempt: Annotated[int, Query(ge=1, le=10)],
) -> Response:
    return await _twiml(renderer, calls.resume(fields.get("CallSid", ""), turn, attempt))


@router.post(_path(STATUS_PATH), status_code=204, include_in_schema=False)
async def post_call_status(fields: SignedForm, calls: Calls) -> Response:
    duration = fields.get("CallDuration", "")
    try:
        await calls.complete(
            fields.get("CallSid", ""),
            fields.get("CallStatus", ""),
            int(duration) if duration.isdigit() else None,
        )
    except Exception as exc:
        # Twilio doesn't retry status callbacks; the idle sweep closes the call later.
        logger.error("call status webhook failed", extra={"error_type": type(exc).__name__})
    return Response(status_code=204)
