"""Dependencies for phone numbers and voice calls (slice 14).

Builds `PhoneNumberService` and `CallSessionService` for a request, plus the stores that
speak those services' dataclasses (kept in `core`, like `core/directory.py`). Everything
is overridable in tests: `get_numbers_api` (a `FakeNumbersApi`), `get_session_factory`,
`get_voice_settings`, and the shared ones (`get_llm_client`, `get_usage_guard`,
`get_clock`, the calendar harness, `get_sms_api`).

A voice turn runs on a session of its OWN (`get_session_factory`), because it may keep
running after the webhook that started it has answered Twilio with a holding phrase; the
request's session closes with the request.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.booking_rules import default_booking_rules
from core.calendar import (
    get_calendar_api,
    get_calendar_config,
    get_clock,
    get_oauth_state_signer,
    get_token_vault,
)
from core.config import settings
from core.conversation import CalendarDeps, build_conversation_engine, get_conversation_engine
from core.db import async_session_factory, get_session
from core.llm import get_llm_client
from core.media import MEDIA_PATH
from core.speech import get_speech_synthesizer
from core.summaries import build_summarizer
from core.tenant import Tenant
from core.twilio import get_notifier
from core.usage import get_usage_guard
from integrations.google_calendar import GoogleCalendarApi
from integrations.llm.port import LLMClient
from integrations.twilio.numbers import HttpTwilioNumbersApi
from integrations.twilio.port import NumbersApi
from models.interaction import Interaction
from models.phone_number import PhoneNumber
from models.voice_turn import VoiceTurn
from repositories import business as business_repo
from repositories import interactions as interactions_repo
from repositories import phone_numbers as numbers_repo
from repositories import users as users_repo
from repositories import voice_turns as turns_repo
from repositories.agents import SqlAgentStore
from repositories.billing import SqlBillingStore
from repositories.interactions import InteractionBusyError, SqlConversationStore
from repositories.phone_numbers import SqlPhoneNumberStore
from services.agent import load_agent
from services.billing import access_for
from services.booking_rules import BookingRules
from services.calendar import CalendarConfig
from services.call_session import (
    CallSessionService,
    CallTarget,
    TurnClaim,
    VoiceSettings,
)
from services.conversation import ConversationEngine, TurnResult
from services.lifecycle import InteractionLifecycle
from services.notifier import Notifier
from services.oauth_state import OAuthStateSigner
from services.phone_numbers import PhoneNumberService, WebhookTargets
from services.speech import SpeechSynthesizer
from services.token_vault import TokenVault
from services.twiml import TwimlRenderer, VoiceUrls
from services.usage import ConcurrencyLimitedError, UsageGuard

WEBHOOKS = "/api/v1/webhooks/twilio"
VOICE_PATH = f"{WEBHOOKS}/voice"
INPUT_PATH = f"{WEBHOOKS}/input"
WAIT_PATH = f"{WEBHOOKS}/input/wait"
STATUS_PATH = f"{WEBHOOKS}/status"


def _public(path: str) -> str:
    """The URL Twilio is told to call: absolute on the public origin when known (that is
    what it signs), else the path (Twilio resolves it against the current request)."""
    return f"{settings.api_public_base_url}{path}" if settings.api_public_base_url else path


@lru_cache(maxsize=1)
def _build_numbers_api() -> NumbersApi | None:
    if not settings.twilio_configured:
        return None
    assert settings.twilio_account_sid and settings.twilio_auth_token
    return HttpTwilioNumbersApi(
        settings.twilio_account_sid,
        settings.twilio_auth_token.get_secret_value(),
        settings.twilio_timeout_seconds,
    )


def get_numbers_api() -> NumbersApi | None:
    """One adapter per process (it holds an HTTP pool); `None` when Twilio isn't set up."""
    return _build_numbers_api()


def get_webhook_targets() -> WebhookTargets:
    if not (settings.twilio_configured and settings.api_public_base_url):
        return WebhookTargets(None, None)
    return WebhookTargets(_public(VOICE_PATH), _public(STATUS_PATH))


def get_phone_number_service(
    session: AsyncSession = Depends(get_session),
    api: NumbersApi | None = Depends(get_numbers_api),
    usage: UsageGuard = Depends(get_usage_guard),
    targets: WebhookTargets = Depends(get_webhook_targets),
) -> PhoneNumberService:
    return PhoneNumberService(SqlPhoneNumberStore(session), api, usage, targets)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_session_factory


def get_voice_settings() -> VoiceSettings:
    return VoiceSettings(
        max_call_seconds=settings.max_call_duration_seconds,
        hold_after_seconds=settings.voice_hold_after_seconds,
        wait_seconds=settings.voice_wait_seconds,
        max_holds=settings.voice_max_holds,
    )


def get_twiml_renderer() -> TwimlRenderer:
    return TwimlRenderer(
        VoiceUrls(input=_public(INPUT_PATH), wait=_public(WAIT_PATH), media=_public(MEDIA_PATH))
    )


async def tenant_for_business(session: AsyncSession, business_id: uuid.UUID) -> Tenant | None:
    """The tenant a call belongs to, from the stored business row (never the payload)."""
    business = await business_repo.get_business_for_stored_row(session, business_id)
    if business is None:
        return None
    owner = await users_repo.get_user_by_id(session, business.owner_id)
    if owner is None:
        return None
    return Tenant(user=owner, business=business)


class SqlCallStore:
    """`services.call_session.CallStore` over the webhook request's session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_number(self, phone_number: str) -> PhoneNumber | None:
        return await numbers_repo.find_by_number(self._session, phone_number)

    async def find_call(self, call_sid: str) -> Interaction | None:
        return await interactions_repo.find_voice_call(self._session, call_sid)

    async def call_target(self, business_id: uuid.UUID) -> CallTarget | None:
        tenant = await tenant_for_business(self._session, business_id)
        if tenant is None or tenant.business is None:
            return None
        agent = await load_agent(SqlAgentStore(self._session), tenant.business)
        subscription = await SqlBillingStore(self._session).get_for_business(business_id)
        return CallTarget(
            tenant=tenant,
            greeting=agent.greeting,
            agent_active=agent.active,
            entitled=access_for(subscription).entitled,
            voice=agent.voice,
        )

    async def lock_admission_and_count(
        self, business_id: uuid.UUID, idle_minutes: int
    ) -> tuple[int, int]:
        try:
            return await interactions_repo.lock_admission_and_count(
                self._session, business_id, idle_minutes
            )
        except InteractionBusyError:
            raise ConcurrencyLimitedError("Too many calls are starting at once.") from None

    async def create_call(
        self,
        *,
        business_id: uuid.UUID,
        phone_number_id: uuid.UUID,
        call_sid: str,
        caller: str | None,
        greeting: str,
    ) -> Interaction:
        interaction, created = await interactions_repo.create_voice_call(
            self._session,
            business_id=business_id,
            phone_number_id=phone_number_id,
            call_sid=call_sid,
            caller=caller,
        )
        if created:
            # Agent message 1, as in the test chat; the row is ours until the commit.
            await interactions_repo.append_message(self._session, interaction, "agent", greeting)
        return interaction

    async def next_turn(self, business_id: uuid.UUID, interaction_id: uuid.UUID) -> int:
        return await turns_repo.max_turn(self._session, business_id, interaction_id) + 1

    async def checkpoint(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


def _claim_view(row: VoiceTurn, *, created: bool = False) -> TurnClaim:
    return TurnClaim(
        id=row.id,
        turn=row.turn,
        utterance=row.utterance,
        status=row.status,
        claim=row.claim,
        created=created,
    )


class SqlTurnClaims:
    """`services.call_session.TurnClaims`: each operation its own committed transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def claim(
        self,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        turn: int,
        utterance: str,
        claim: uuid.UUID,
    ) -> TurnClaim:
        async with self._sessions() as session:
            created = await turns_repo.insert_claim(
                session,
                business_id=business_id,
                interaction_id=interaction_id,
                turn=turn,
                utterance=utterance,
                claim=claim,
            )
            await session.commit()
            row = await turns_repo.get_turn(session, business_id, interaction_id, turn)
            assert row is not None
            return _claim_view(row, created=created)

    async def get(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, turn: int
    ) -> TurnClaim | None:
        async with self._sessions() as session:
            row = await turns_repo.get_turn(session, business_id, interaction_id, turn)
            return _claim_view(row) if row is not None else None

    async def heartbeat(self, turn_id: uuid.UUID, claim: uuid.UUID) -> bool:
        async with self._sessions() as session:
            alive = await turns_repo.heartbeat(session, turn_id, claim)
            await session.commit()
            return alive

    async def take_over(
        self, turn_id: uuid.UUID, seen: uuid.UUID, new: uuid.UUID, stale_after_seconds: float
    ) -> bool:
        async with self._sessions() as session:
            won = await turns_repo.take_over(session, turn_id, seen, new, stale_after_seconds)
            await session.commit()
            return won

    async def finish(self, turn_id: uuid.UUID, claim: uuid.UUID, status: str) -> None:
        async with self._sessions() as session:
            await turns_repo.finish(session, turn_id, claim, status)
            await session.commit()


def get_call_session(
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    engine: ConversationEngine = Depends(get_conversation_engine),
    llm: LLMClient = Depends(get_llm_client),
    usage: UsageGuard = Depends(get_usage_guard),
    calendar_api: GoogleCalendarApi = Depends(get_calendar_api),
    vault: TokenVault = Depends(get_token_vault),
    signer: OAuthStateSigner = Depends(get_oauth_state_signer),
    calendar_config: CalendarConfig = Depends(get_calendar_config),
    booking_defaults: BookingRules = Depends(default_booking_rules),
    clock: Callable[[], datetime] = Depends(get_clock),
    notifier: Notifier = Depends(get_notifier),
    voice: VoiceSettings = Depends(get_voice_settings),
    speech: SpeechSynthesizer = Depends(get_speech_synthesizer),
) -> CallSessionService:
    calendar = CalendarDeps(calendar_api, vault, signer, calendar_config)

    async def run_turn(
        business_id: uuid.UUID, interaction_id: uuid.UUID, utterance: str, key: str
    ) -> TurnResult:
        # Its own session and engine: this may outlive the webhook request.
        async with sessions() as own:
            tenant = await tenant_for_business(own, business_id)
            if tenant is None:
                raise LookupError("business_gone")
            turn_engine = build_conversation_engine(
                own,
                llm,
                usage,
                clock=clock,
                calendar=calendar,
                booking_defaults=booking_defaults,
                notifier=notifier,
            )
            result = await turn_engine.handle_turn(
                tenant, interaction_id, utterance, "voice", turn_key=key
            )
            await own.commit()
            return result

    async def replay(tenant: Tenant, interaction_id: uuid.UUID, key: str) -> TurnResult | None:
        # Another worker may have stored the turn since this session last read the row.
        # Refreshed in place, never expired: the service still reads loaded objects.
        assert tenant.business is not None
        await interactions_repo.reload_interaction(session, tenant.business.id, interaction_id)
        return await engine.replay(tenant, interaction_id, key, "voice")

    lifecycle = InteractionLifecycle(
        SqlConversationStore(session),
        build_summarizer(session, llm, usage, clock=clock),
        idle_minutes=usage.interaction_idle_minutes,
    )

    async def end(tenant: Tenant, interaction_id: uuid.UUID) -> bool:
        return await lifecycle.end(tenant, interaction_id, "voice")

    return CallSessionService(
        SqlCallStore(session),
        SqlTurnClaims(sessions),
        usage,
        run_turn=run_turn,
        replay=replay,
        end=end,
        settings=voice,
        speech=speech,
        # Call duration is wall time; `clock` is the business clock tests may pin.
    )
