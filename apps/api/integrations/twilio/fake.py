"""In-memory `SmsApi` and `NumbersApi` for tests: record calls, failures are scriptable."""

import uuid
from dataclasses import dataclass, replace

from integrations.twilio.port import (
    REASON_NOT_FOUND,
    AccountInfo,
    IncomingNumber,
    SentSms,
    TwilioApiError,
)


@dataclass(frozen=True)
class SentRecord:
    to: str
    body: str
    status_callback: str | None
    sid: str


class FakeSmsApi:
    def __init__(self, *, status: str = "queued") -> None:
        self.status = status  # the status every successful send returns
        self.sent: list[SentRecord] = []
        self.attempts = 0  # every call, failed or not
        self._failures: list[TwilioApiError] = []

    def fail_next(self, error: TwilioApiError, *, times: int = 1) -> None:
        self._failures.extend([error] * times)

    async def send_sms(self, *, to: str, body: str, status_callback: str | None) -> SentSms:
        self.attempts += 1
        if self._failures:
            raise self._failures.pop(0)
        # Twilio's shape (SM + 32 hex), random: `escalations.sms_sid` is unique across
        # every test running in parallel on the same database.
        sid = f"SM{uuid.uuid4().hex}"
        self.sent.append(SentRecord(to, body, status_callback, sid))
        return SentSms(sid=sid, status=self.status)


def random_number_sid() -> str:
    """Twilio's shape (PN + 32 hex), random: `phone_numbers.provider_number_sid` is unique
    across every test running in parallel on the same database."""
    return f"PN{uuid.uuid4().hex}"


def random_phone_number() -> str:
    """A synthetic +1 555 E.164 number with random digits, unique enough for parallel
    tests sharing `phone_numbers.phone_number`."""
    return f"+1555{uuid.uuid4().int % 10**7:07d}"


class FakeNumbersApi:
    """The account's numbers in memory. `add_number` puts one on the account; `calls` lists
    every operation attempted (failed ones included), as `(op, sid | None)`."""

    def __init__(self, *, trial: bool = True) -> None:
        self.trial = trial
        self.numbers: dict[str, IncomingNumber] = {}
        self.calls: list[tuple[str, str | None]] = []
        self._failures: dict[str, list[TwilioApiError]] = {}

    def add_number(
        self, phone_number: str | None = None, *, sid: str | None = None, friendly_name=None
    ) -> IncomingNumber:
        number = IncomingNumber(
            sid=sid or random_number_sid(),
            phone_number=phone_number or random_phone_number(),
            friendly_name=friendly_name,
            voice_url=None,
            voice_method=None,
            status_callback=None,
            status_callback_method=None,
        )
        self.numbers[number.sid] = number
        return number

    def release(self, sid: str) -> None:
        """The number left the account outside Tether (released in the console)."""
        self.numbers.pop(sid, None)

    def edit_externally(self, sid: str, **changes: str | None) -> None:
        """Someone changed the number's config in the Twilio console."""
        self.numbers[sid] = replace(self.numbers[sid], **changes)

    def fail_next(self, op: str, error: TwilioApiError, *, times: int = 1) -> None:
        self._failures.setdefault(op, []).extend([error] * times)

    def _attempt(self, op: str, sid: str | None = None) -> None:
        self.calls.append((op, sid))
        queue = self._failures.get(op)
        if queue:
            raise queue.pop(0)

    async def account(self) -> AccountInfo:
        self._attempt("account")
        return AccountInfo(trial=self.trial)

    async def list_numbers(self) -> list[IncomingNumber]:
        self._attempt("list_numbers")
        return list(self.numbers.values())

    async def get_number(self, sid: str) -> IncomingNumber:
        self._attempt("get_number", sid)
        if sid not in self.numbers:
            raise TwilioApiError(REASON_NOT_FOUND, error_code="20404")
        return self.numbers[sid]

    async def set_webhooks(
        self, sid: str, *, voice_url: str, status_callback: str
    ) -> IncomingNumber:
        self._attempt("set_webhooks", sid)
        if sid not in self.numbers:
            raise TwilioApiError(REASON_NOT_FOUND, error_code="20404")
        updated = replace(
            self.numbers[sid],
            voice_url=voice_url or None,
            voice_method="POST",
            status_callback=status_callback or None,
            status_callback_method="POST",
        )
        self.numbers[sid] = updated
        return updated
