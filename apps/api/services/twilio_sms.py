"""`TwilioSmsNotifier`: one escalation SMS through the Twilio port (slice 13).

`retry_bounded(SMS_RETRY)` around `usage.metered(UsageKind.SMS)` around ONE adapter
attempt, so each attempt is reserved and counted. Only failures where Twilio certainly
created no message are retried (connect errors, 429). No message body or number is logged.
"""

from typing import cast

from core.retry import RetryPolicy, retry_bounded
from integrations.twilio.port import SentSms, SmsApi, TwilioApiError, is_retryable
from services.notifier import Alert, NotifierError, SendResult, SendStatus, alert_body
from services.usage import LimitExceededError, UsageGuard, UsageKind, UsageUnavailableError

SMS_RETRY = RetryPolicy(2, 0.5)

_SEND_STATUSES = ("accepted", "queued", "sending", "sent")


class TwilioSmsNotifier:
    def __init__(
        self,
        api: SmsApi,
        usage: UsageGuard,
        *,
        status_callback_url: str | None,
        retry: RetryPolicy = SMS_RETRY,
    ) -> None:
        self._api = api
        self._usage = usage
        self._callback = status_callback_url
        self._retry = retry

    async def send_alert(self, alert: Alert) -> SendResult:
        body = alert_body(alert.reason, alert.inbox_url)

        async def attempt() -> SentSms:
            async with self._usage.metered(
                UsageKind.SMS,
                business_id=alert.business_id,
                interaction_id=alert.interaction_id,
            ):
                return await self._api.send_sms(
                    to=alert.to, body=body, status_callback=self._callback
                )

        try:
            sent = await retry_bounded(attempt, self._retry, is_retryable)
        except LimitExceededError:
            raise NotifierError("limit_reached") from None
        except UsageUnavailableError:
            raise NotifierError("usage_unavailable") from None
        except TwilioApiError as exc:
            raise NotifierError(exc.reason) from None
        status = sent.status if sent.status in _SEND_STATUSES else "accepted"
        return SendResult(status=cast(SendStatus, status), provider_id=sent.sid)
