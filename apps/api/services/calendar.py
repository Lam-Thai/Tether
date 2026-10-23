"""Google Calendar: the gateway every calendar consumer uses, and the owner-facing
connection lifecycle.

Two deep modules:

- `CalendarGateway` (implemented by `GoogleCalendarGateway`): what booking (slices
  10-11) calls. Give it a `CalendarConnection` and it hides decryption of the stored
  refresh token, access-token refresh (once per method call; no access token is ever
  stored), bounded retries wrapped AROUND `UsageGuard.metered` (kind `calendar_call`,
  so every attempt is reserved and counted), and error mapping to three domain errors.
- `CalendarConnections`: connect URL, OAuth completion, status, calendar choice,
  disconnect. It owns the rules about what the owner is told and what is stored.

The raw Google client is `integrations/google_calendar` (single attempt, plaintext
tokens). No FastAPI or SQLAlchemy imports here: the store is a port.
"""

import base64
import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from core.errors import AppError
from core.retry import RetryPolicy, retry_bounded
from integrations.google_calendar.port import (
    REQUIRED_SCOPES,
    BusyInterval,
    CalendarInfo,
    CreatedEvent,
    EventDraft,
    GoogleApiError,
    GoogleCalendarApi,
    OAuthGrant,
    TimeWindow,
    is_retryable,
)
from models.calendar import CalendarConnection as CalendarRow
from services.oauth_state import InvalidOAuthStateError, OAuthStateSigner, code_challenge
from services.token_vault import SealedToken, TokenDecryptError, TokenVault
from services.usage import LimitExceededError, UsageGuard, UsageKind, UsageUnavailableError

logger = logging.getLogger("tether.calendar")

# At most 2 attempts per Google call, retrying only `GoogleApiError.retryable`.
CALENDAR_RETRY = RetryPolicy(attempts=2, backoff_seconds=0.5)
# A single-use authorization code gets exactly one attempt.
EXCHANGE_ONCE = RetryPolicy(attempts=1)

NOT_CONNECTED = "not_connected"
NEEDS_SELECTION = "needs_selection"
CONNECTED = "connected"
RECONNECT_NEEDED = "reconnect_needed"

# Fixed `last_error` codes; never provider text.
ERR_AUTH_REVOKED = "auth_revoked"
ERR_TOKEN_UNREADABLE = "token_unreadable"
ERR_UNAVAILABLE = "unavailable"
ERR_CALENDAR_MISSING = "calendar_missing"

_NAME_MAX = 255


# --- errors the gateway raises (domain, not HTTP) ---------------------------------


class CalendarError(Exception):
    """`reason` is the fixed code stored as `last_error`; `detail` is the provider's fixed
    reason code (`GoogleApiError.reason`, e.g. `invalid_grant`) when there was one."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class CalendarAuthRevokedError(CalendarError):
    """The owner's grant is gone (revoked, expired, scopes removed) or the stored token
    can't be read. `reason` is `auth_revoked` or `token_unreadable`. Only reconnecting helps."""


class CalendarUnavailableError(CalendarError):
    """Google couldn't be used right now: failed after bounded retries, a usage limit,
    or the usage store down. Not the owner's fault; nothing was confirmed."""


class CalendarNotFoundError(CalendarError):
    """The calendar or event doesn't exist (any more)."""


class CalendarNotSelectedError(CalendarError):
    """The connection has no calendar chosen yet."""


# --- errors the connection service raises (HTTP-facing) ---------------------------


class CalendarNotConfiguredError(AppError):
    status_code = 503
    code = "calendar_not_configured"

    def __init__(self) -> None:
        super().__init__("Google Calendar isn't set up on this server yet.")


class CalendarNotConnectedError(AppError):
    status_code = 404
    code = "calendar_not_connected"

    def __init__(self) -> None:
        super().__init__("Google Calendar isn't connected.")


class CalendarSelectionNotFoundError(AppError):
    status_code = 404
    code = "calendar_not_found"

    def __init__(self) -> None:
        super().__init__("That calendar isn't one of your own Google calendars.")


class CalendarReconnectNeededError(AppError):
    status_code = 409
    code = "calendar_reconnect_needed"

    def __init__(self) -> None:
        super().__init__("Google Calendar access was lost. Reconnect to continue.")


class CalendarScopesMissingError(AppError):
    status_code = 400
    code = "calendar_scopes_missing"

    def __init__(self) -> None:
        super().__init__(
            "Tether needs every requested Google Calendar permission. Please try again."
        )


class CalendarGrantIncompleteError(AppError):
    status_code = 400
    code = "calendar_grant_incomplete"

    def __init__(self) -> None:
        super().__init__("Google didn't grant lasting access. Please try again.")


class CalendarUnreachableError(AppError):
    status_code = 503
    code = "calendar_unavailable"

    def __init__(self) -> None:
        super().__init__("Google Calendar can't be reached right now. Please try again shortly.")


# --- value types -------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarConnection:
    """What a gateway call needs, built from the stored row. Holds the SEALED token."""

    business_id: uuid.UUID
    sealed_token: SealedToken
    calendar_id: str | None
    status: str


@dataclass(frozen=True)
class CalendarStatus:
    status: str
    calendar_id: str | None = None
    calendar_name: str | None = None
    last_error: str | None = None
    last_synced_at: datetime | None = None


@dataclass(frozen=True)
class DisconnectResult:
    revoked: bool


@dataclass(frozen=True)
class TenantIds:
    business_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class CalendarConfig:
    client_configured: bool
    redirect_uri: str
    status_check_seconds: int


def token_context(business_id: uuid.UUID) -> str:
    """The vault's additional authenticated data for a business's token."""
    return f"calendar:{business_id}"


def connection_from_row(row: CalendarRow) -> CalendarConnection:
    return CalendarConnection(
        business_id=row.business_id,
        sealed_token=SealedToken(row.token_key_id, bytes(row.encrypted_refresh_token)),
        calendar_id=row.calendar_id,
        status=row.connection_status,
    )


def event_id_for(business_id: uuid.UUID, idempotency_key: str) -> str:
    """A Google-valid event id (base32hex, lowercase) derived from the idempotency key, so a
    retried booking hits Google's duplicate check instead of creating a second event."""
    digest = hashlib.sha256(f"{business_id}:{idempotency_key}".encode()).digest()
    return base64.b32hexencode(digest).decode().lower().rstrip("=")


# --- the metered, retried, error-mapped Google call --------------------------------


async def metered_google_call[T](
    usage: UsageGuard,
    business_id: uuid.UUID,
    call: Callable[[], Awaitable[T]],
    policy: RetryPolicy = CALENDAR_RETRY,
) -> T:
    """`retry_bounded` around `usage.metered(CALENDAR_CALL)` around ONE provider attempt.
    A reached limit (or an unreadable usage store) is never retried."""

    async def attempt() -> T:
        async with usage.metered(UsageKind.CALENDAR_CALL, business_id=business_id):
            return await call()

    try:
        return await retry_bounded(attempt, policy, is_retryable)
    except GoogleApiError as exc:
        if exc.auth_revoked:
            raise CalendarAuthRevokedError(ERR_AUTH_REVOKED, exc.reason) from None
        if exc.not_found:
            raise CalendarNotFoundError(exc.reason, exc.reason) from None
        raise CalendarUnavailableError(exc.reason, exc.reason) from None
    except (LimitExceededError, UsageUnavailableError):
        raise CalendarUnavailableError("usage") from None


class CalendarGateway(Protocol):
    """What every calendar consumer uses (owner UI, availability, booking)."""

    async def list_calendars(self, conn: CalendarConnection) -> list[CalendarInfo]:
        """The owner's OWN calendars only (access role owner)."""
        ...

    async def free_busy(self, conn: CalendarConnection, window: TimeWindow) -> list[BusyInterval]:
        """Busy blocks on the selected calendar."""
        ...

    async def create_event(
        self, conn: CalendarConnection, draft: EventDraft, idempotency_key: str
    ) -> CreatedEvent:
        """Creates an event on the selected calendar. The same key never creates a second one."""
        ...

    async def cancel_event(self, conn: CalendarConnection, event_id: str) -> None: ...

    async def revoke(self, conn: CalendarConnection) -> bool:
        """True when Google confirmed (or the token was already revoked); False when it
        couldn't be confirmed (transient failure, unreadable token)."""
        ...


class GoogleCalendarGateway:
    def __init__(
        self,
        api: GoogleCalendarApi,
        vault: TokenVault,
        usage: UsageGuard,
        retry: RetryPolicy = CALENDAR_RETRY,
    ) -> None:
        self._api = api
        self._vault = vault
        self._usage = usage
        self._retry = retry

    async def list_calendars(self, conn: CalendarConnection) -> list[CalendarInfo]:
        token = await self._access_token(conn)
        calendars = await self._call(conn, lambda: self._api.list_calendars(token))
        return [c for c in calendars if c.access_role == "owner"]

    async def free_busy(self, conn: CalendarConnection, window: TimeWindow) -> list[BusyInterval]:
        calendar_id = _selected(conn)
        token = await self._access_token(conn)
        return await self._call(conn, lambda: self._api.free_busy(token, calendar_id, window))

    async def create_event(
        self, conn: CalendarConnection, draft: EventDraft, idempotency_key: str
    ) -> CreatedEvent:
        calendar_id = _selected(conn)
        event_id = event_id_for(conn.business_id, idempotency_key)
        token = await self._access_token(conn)
        return await self._call(
            conn, lambda: self._api.insert_event(token, calendar_id, draft, event_id)
        )

    async def cancel_event(self, conn: CalendarConnection, event_id: str) -> None:
        calendar_id = _selected(conn)
        token = await self._access_token(conn)
        await self._call(conn, lambda: self._api.delete_event(token, calendar_id, event_id))

    async def revoke(self, conn: CalendarConnection) -> bool:
        try:
            refresh_token = self._open(conn)
            await self._call(conn, lambda: self._api.revoke(refresh_token))
        except CalendarError:
            return False
        return True

    def _open(self, conn: CalendarConnection) -> str:
        try:
            return self._vault.decrypt(conn.sealed_token, context=token_context(conn.business_id))
        except TokenDecryptError:
            raise CalendarAuthRevokedError(ERR_TOKEN_UNREADABLE) from None

    async def _access_token(self, conn: CalendarConnection) -> str:
        refresh_token = self._open(conn)
        access = await self._call(conn, lambda: self._api.refresh_access_token(refresh_token))
        return access.token

    async def _call[T](self, conn: CalendarConnection, call: Callable[[], Awaitable[T]]) -> T:
        return await metered_google_call(self._usage, conn.business_id, call, self._retry)


def _selected(conn: CalendarConnection) -> str:
    if conn.calendar_id is None:
        raise CalendarNotSelectedError("no_calendar_selected")
    return conn.calendar_id


# --- the owner-facing lifecycle ----------------------------------------------------


class CalendarStore(Protocol):
    """`repositories/calendar.SqlCalendarStore`. Every method is scoped to one business."""

    async def get(self, business_id: uuid.UUID) -> CalendarRow | None: ...

    async def upsert_tokens(
        self,
        business_id: uuid.UUID,
        *,
        encrypted_refresh_token: bytes,
        token_key_id: str,
        granted_scopes: str,
        synced_at: datetime,
    ) -> CalendarRow:
        """Replaces any stored token; clears the calendar choice; status `needs_selection`."""
        ...

    async def set_selection(
        self,
        business_id: uuid.UUID,
        *,
        calendar_id: str,
        calendar_name: str,
        synced_at: datetime,
        seen_updated_at: datetime,
    ) -> CalendarRow | None:
        """Conditional on the row still having `seen_updated_at` (nobody reconnected or
        changed it since it was read). Returns the row as it is now, or None if it is gone."""
        ...

    async def mark_status(
        self,
        business_id: uuid.UUID,
        *,
        status: str,
        last_error: str | None,
        last_synced_at: datetime | None,
        seen_updated_at: datetime,
        clear_selection: bool = False,
    ) -> CalendarRow | None:
        """Conditional like `set_selection`; returns the current row (None if gone)."""
        ...

    async def reseal(
        self,
        business_id: uuid.UUID,
        *,
        encrypted_refresh_token: bytes,
        token_key_id: str,
        seen_updated_at: datetime,
    ) -> CalendarRow | None:
        """Replaces the sealed token only if the row is unchanged since it was read."""
        ...

    async def delete(self, business_id: uuid.UUID) -> CalendarRow | None: ...

    async def checkpoint(self) -> None:
        """Commit, so no transaction is open across a provider call and a state change
        survives a request that then fails."""
        ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _status_of(row: CalendarRow | None) -> CalendarStatus:
    if row is None:
        return CalendarStatus(NOT_CONNECTED)
    return CalendarStatus(
        status=row.connection_status,
        calendar_id=row.calendar_id,
        calendar_name=row.calendar_name,
        last_error=row.last_error,
        last_synced_at=row.last_synced_at,
    )


class CalendarConnections:
    def __init__(
        self,
        store: CalendarStore,
        gateway: CalendarGateway,
        api: GoogleCalendarApi,
        vault: TokenVault,
        signer: OAuthStateSigner,
        usage: UsageGuard,
        config: CalendarConfig,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._api = api
        self._vault = vault
        self._signer = signer
        self._usage = usage
        self._config = config
        self._clock = clock

    def connect_url(self, tenant_ids: TenantIds) -> str:
        self._require_configured()
        issued = self._signer.issue(tenant_ids.business_id, tenant_ids.user_id)
        return self._api.authorization_url(
            state=issued.state,
            code_challenge=code_challenge(issued.code_verifier),
            redirect_uri=self._config.redirect_uri,
        )

    async def complete(self, tenant_ids: TenantIds, *, code: str, state: str) -> CalendarStatus:
        self._require_configured()
        business_id = tenant_ids.business_id
        verifier = self._signer.verify(state, business_id=business_id, user_id=tenant_ids.user_id)
        await self._store.checkpoint()

        grant = await self._exchange(business_id, code, verifier)
        # A rejected grant is never revoked: Google's revoke kills the whole grant for this
        # account and client, which would also kill a working connection's token.
        if grant.refresh_token is None:
            raise CalendarGrantIncompleteError
        if not REQUIRED_SCOPES <= grant.scopes:
            raise CalendarScopesMissingError

        sealed = self._vault.encrypt(grant.refresh_token, context=token_context(business_id))
        row = await self._store.upsert_tokens(
            business_id,
            encrypted_refresh_token=sealed.ciphertext,
            token_key_id=sealed.key_id,
            granted_scopes=" ".join(sorted(grant.scopes)),
            synced_at=self._clock(),
        )
        status = _status_of(row)
        await self._store.checkpoint()
        # The previous token (if any) is simply overwritten, never revoked at Google: the
        # same Google account shares one grant, so revoking it would kill the new token too.
        return status

    async def status(self, business_id: uuid.UUID) -> CalendarStatus:
        row = await self._store.get(business_id)
        if row is None:
            return CalendarStatus(NOT_CONNECTED)
        if (
            not self._config.client_configured
            or row.connection_status not in (NEEDS_SELECTION, CONNECTED)
            or not self._is_stale(row)
        ):
            return _status_of(row)
        await self._store.checkpoint()
        try:
            owned = await self._gateway.list_calendars(connection_from_row(row))
        except CalendarAuthRevokedError as exc:
            return _status_of(await self._mark_reconnect(row, exc.reason))
        except CalendarError:
            return _status_of(
                await self._store.mark_status(
                    business_id,
                    status=row.connection_status,
                    last_error=ERR_UNAVAILABLE,
                    last_synced_at=row.last_synced_at,
                    seen_updated_at=row.updated_at,
                )
            )
        if row.token_key_id != self._vault.active_key_id:
            # The probe succeeded, so the token is good: move it to the active key.
            resealed = await self._reseal(row)
            if resealed is None:
                return _status_of(await self._store.get(business_id))  # changed underneath
            row = resealed
        now = self._clock()
        if row.calendar_id is not None and all(c.id != row.calendar_id for c in owned):
            updated = await self._store.mark_status(
                business_id,
                status=NEEDS_SELECTION,
                last_error=ERR_CALENDAR_MISSING,
                last_synced_at=now,
                seen_updated_at=row.updated_at,
                clear_selection=True,
            )
        else:
            updated = await self._store.mark_status(
                business_id,
                status=row.connection_status,
                last_error=None,
                last_synced_at=now,
                seen_updated_at=row.updated_at,
            )
        return _status_of(updated)

    async def calendars(self, business_id: uuid.UUID) -> list[CalendarInfo]:
        self._require_configured()
        row = await self._usable_row(business_id)
        return await self._owned_calendars(row)

    async def select(self, business_id: uuid.UUID, calendar_id: str) -> CalendarStatus:
        self._require_configured()
        row = await self._usable_row(business_id)
        chosen = next((c for c in await self._owned_calendars(row) if c.id == calendar_id), None)
        if chosen is None:
            raise CalendarSelectionNotFoundError
        updated = await self._store.set_selection(
            business_id,
            calendar_id=chosen.id,
            calendar_name=chosen.name[:_NAME_MAX],
            synced_at=self._clock(),
            seen_updated_at=row.updated_at,
        )
        if updated is None:
            raise CalendarNotConnectedError  # disconnected while we were asking Google
        if updated.connection_status != CONNECTED or updated.calendar_id != chosen.id:
            # Reconnected (or otherwise changed) since we read it: the choice was made against
            # an old grant, so it is not applied.
            raise CalendarReconnectNeededError
        return _status_of(updated)

    async def free_busy(self, business_id: uuid.UUID, window: TimeWindow) -> list[BusyInterval]:
        """Busy blocks on the chosen calendar, for availability (`services/availability.py`).

        Raises gateway errors, never HTTP ones: `CalendarNotSelectedError` when the feature
        isn't configured, nothing is connected or no calendar is chosen;
        `CalendarAuthRevokedError` when the connection needs a reconnect (already marked,
        or revoked during this call, which marks it and commits before raising); any
        other `CalendarError` when Google couldn't be used. Commits before calling Google.
        """
        if not self._config.client_configured:
            raise CalendarNotSelectedError("not_configured")
        row = await self._store.get(business_id)
        if row is None:
            raise CalendarNotSelectedError("not_connected")
        if row.connection_status == RECONNECT_NEEDED:
            raise CalendarAuthRevokedError(row.last_error or ERR_AUTH_REVOKED)
        if row.connection_status != CONNECTED or row.calendar_id is None:
            raise CalendarNotSelectedError("no_calendar_selected")
        conn = connection_from_row(row)
        await self._store.checkpoint()
        try:
            return await self._gateway.free_busy(conn, window)
        except CalendarAuthRevokedError as exc:
            await self._mark_reconnect(row, exc.reason)
            await self._store.checkpoint()  # the state survives whatever happens next
            raise
        except CalendarNotFoundError:
            # The chosen calendar is gone: the owner has to pick another one.
            await self._store.mark_status(
                business_id,
                status=NEEDS_SELECTION,
                last_error=ERR_CALENDAR_MISSING,
                last_synced_at=self._clock(),
                seen_updated_at=row.updated_at,
                clear_selection=True,
            )
            await self._store.checkpoint()
            raise CalendarNotSelectedError(ERR_CALENDAR_MISSING) from None

    async def disconnect(self, business_id: uuid.UUID) -> DisconnectResult:
        self._require_configured()
        row = await self._store.get(business_id)
        if row is None:
            raise CalendarNotConnectedError
        conn = connection_from_row(row)
        await self._store.checkpoint()
        revoked = await self._gateway.revoke(conn)
        await self._store.delete(business_id)
        return DisconnectResult(revoked=revoked)

    # --- internals -----------------------------------------------------------------

    def _require_configured(self) -> None:
        if not self._config.client_configured:
            raise CalendarNotConfiguredError

    def _is_stale(self, row: CalendarRow) -> bool:
        if row.last_synced_at is None:
            return True
        age = (self._clock() - row.last_synced_at).total_seconds()
        return age >= self._config.status_check_seconds

    async def _usable_row(self, business_id: uuid.UUID) -> CalendarRow:
        row = await self._store.get(business_id)
        if row is None:
            raise CalendarNotConnectedError
        if row.connection_status == RECONNECT_NEEDED:
            raise CalendarReconnectNeededError
        return row

    async def _owned_calendars(self, row: CalendarRow) -> list[CalendarInfo]:
        conn = connection_from_row(row)
        await self._store.checkpoint()
        try:
            return await self._gateway.list_calendars(conn)
        except CalendarAuthRevokedError as exc:
            await self._mark_reconnect(row, exc.reason)
            await self._store.checkpoint()  # the state survives the failing request
            raise CalendarReconnectNeededError from None
        except CalendarError:
            raise CalendarUnreachableError from None

    async def _mark_reconnect(self, row: CalendarRow, reason: str) -> CalendarRow | None:
        return await self._store.mark_status(
            row.business_id,
            status=RECONNECT_NEEDED,
            last_error=reason,
            last_synced_at=self._clock(),
            seen_updated_at=row.updated_at,
        )

    async def _reseal(self, row: CalendarRow) -> CalendarRow | None:
        """Re-encrypts the stored token under the active key. None when the row changed
        underneath (a reconnect or disconnect), in which case nothing was written."""
        context = token_context(row.business_id)
        try:
            plain = self._vault.decrypt(connection_from_row(row).sealed_token, context=context)
        except TokenDecryptError:
            return row  # the probe just worked, so this can't happen; leave it as it is
        sealed = self._vault.encrypt(plain, context=context)
        current = await self._store.reseal(
            row.business_id,
            encrypted_refresh_token=sealed.ciphertext,
            token_key_id=sealed.key_id,
            seen_updated_at=row.updated_at,
        )
        if current is None or bytes(current.encrypted_refresh_token) != sealed.ciphertext:
            return None
        return current

    async def _exchange(self, business_id: uuid.UUID, code: str, verifier: str) -> OAuthGrant:
        try:
            return await metered_google_call(
                self._usage,
                business_id,
                lambda: self._api.exchange_code(
                    code=code, code_verifier=verifier, redirect_uri=self._config.redirect_uri
                ),
                EXCHANGE_ONCE,
            )
        except CalendarAuthRevokedError as exc:
            if exc.detail == "invalid_grant":  # the code is wrong, used or expired
                raise InvalidOAuthStateError from None
            raise CalendarUnreachableError from None  # e.g. a misconfigured client secret
        except CalendarError:
            raise CalendarUnreachableError from None
