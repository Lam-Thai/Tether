"""Dependencies for the Google Calendar routes (like `core/billing.py` / `core/llm.py`).

Every piece is its own dependency so tests override them with a `FakeGoogleCalendarApi`
and test keys. The feature is configured only when the client id, client secret, token
keys and state secret are all set (`Settings.calendar_configured`). Otherwise these
return inert placeholders (an API that raises `calendar_not_configured`, a vault and
signer on throwaway keys that can't decrypt anything stored) and `CalendarConnections`
refuses every operation but `status`, so no token can ever be sealed with a key nobody
holds.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.booking_rules import default_booking_rules
from core.config import parse_token_keys, settings
from core.db import get_session
from core.directory import SqlDirectoryStore
from core.usage import get_usage_guard
from integrations.google_calendar import GoogleCalendarApi, HttpGoogleCalendarApi
from repositories.calendar import SqlCalendarStore
from services.availability import AvailabilityService
from services.booking_rules import BookingRules
from services.calendar import (
    CalendarConfig,
    CalendarConnections,
    CalendarNotConfiguredError,
    GoogleCalendarGateway,
)
from services.directory import BusinessDirectory
from services.oauth_state import OAuthStateSigner
from services.token_vault import TokenVault
from services.usage import UsageGuard


class _Unconfigured:
    def __getattr__(self, name: str):
        raise CalendarNotConfiguredError


def _build_api() -> GoogleCalendarApi:
    if not (settings.calendar_configured and settings.google_oauth_client_id):
        return cast(GoogleCalendarApi, _Unconfigured())
    assert settings.google_oauth_client_secret is not None
    return HttpGoogleCalendarApi(
        settings.google_oauth_client_id,
        settings.google_oauth_client_secret.get_secret_value(),
        settings.google_calendar_timeout_seconds,
    )


def _build_vault() -> TokenVault:
    if settings.calendar_token_keys is None:
        return TokenVault({"unconfigured": secrets.token_bytes(32)}, "unconfigured")
    keys, active = parse_token_keys(settings.calendar_token_keys.get_secret_value())
    return TokenVault(keys, active)


def _build_signer() -> OAuthStateSigner:
    secret = (
        settings.oauth_state_secret.get_secret_value()
        if settings.oauth_state_secret
        else secrets.token_urlsafe(48)
    )
    return OAuthStateSigner(secret, settings.calendar_oauth_state_ttl_seconds)


# One of each per process (they hold keys and an HTTP pool, never request state).
_api = _build_api()
_vault = _build_vault()
_signer = _build_signer()


def get_calendar_api() -> GoogleCalendarApi:
    return _api


def get_token_vault() -> TokenVault:
    return _vault


def get_oauth_state_signer() -> OAuthStateSigner:
    return _signer


def get_calendar_config() -> CalendarConfig:
    return CalendarConfig(
        client_configured=settings.calendar_configured,
        redirect_uri=settings.calendar_redirect_uri,
        status_check_seconds=settings.calendar_status_check_seconds,
    )


def get_calendar_connections(
    session: AsyncSession = Depends(get_session),
    api: GoogleCalendarApi = Depends(get_calendar_api),
    vault: TokenVault = Depends(get_token_vault),
    signer: OAuthStateSigner = Depends(get_oauth_state_signer),
    config: CalendarConfig = Depends(get_calendar_config),
    usage: UsageGuard = Depends(get_usage_guard),
) -> CalendarConnections:
    return CalendarConnections(
        store=SqlCalendarStore(session),
        gateway=GoogleCalendarGateway(api, vault, usage),
        api=api,
        vault=vault,
        signer=signer,
        usage=usage,
        config=config,
    )


def get_clock() -> Callable[[], datetime]:
    """ "Now" for availability; a dependency so tests can pin it."""
    return lambda: datetime.now(UTC)


def get_availability_service(
    session: AsyncSession = Depends(get_session),
    connections: CalendarConnections = Depends(get_calendar_connections),
    booking_defaults: BookingRules = Depends(default_booking_rules),
    clock: Callable[[], datetime] = Depends(get_clock),
) -> AvailabilityService:
    directory = BusinessDirectory(SqlDirectoryStore(session), clock, booking_defaults)
    return AvailabilityService(directory, connections)
