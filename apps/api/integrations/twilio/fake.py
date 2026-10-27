"""In-memory `SmsApi` for tests: records sends, failures are scriptable."""

import uuid
from dataclasses import dataclass

from integrations.twilio.port import SentSms, TwilioApiError


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
