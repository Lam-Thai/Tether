"""`ProviderChaos`: inject timeouts, 429s and 5xx responses into the provider fakes.

    chaos = ProviderChaos()
    llm = chaos.wrap("gemini", FakeLLMClient())
    chaos.fail("gemini", "rate_limited", times=1)   # the next call only
    chaos.fail("calendar", "timeout", op="insert_event", lands=True)   # until healed
    chaos.calls("gemini")                           # provider ATTEMPTS actually made
    chaos.heal("gemini")

A fault raises the exception the REAL adapter raises for that HTTP outcome, built from the
table below (one row per adapter; `test_chaos_mapping.py` runs the real adapters over
`httpx.MockTransport` / stub SDKs and fails if a row drifts from them). So retry and
fallback code sees exactly what production sees, and a test that asserts "an SMS timeout
is attempted once" is asserting the adapter's real classification, not the fixture's.

`calls(provider)` counts every attempt that reached the wrapped client, injected failures
included (the provider was called either way), so "bounded retries" is an assertion on a
number the code under test cannot influence. `lands=True` models "the provider did the
work but the response was lost": the wrapped call runs first, then the fault is raised
(an event created at Google whose reply timed out).

Every injected exception carries a canary in its `__cause__` (never in the message, which
real adapters keep fixed), so a test can assert provider text never reaches a log record,
a response body or a stored row.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from integrations.google_calendar.port import GoogleApiError
from integrations.llm.port import LLMUnavailableError
from integrations.stt.port import SttUnavailableError
from integrations.tts.port import TtsUnavailableError
from integrations.twilio.port import TwilioApiError

Provider = Literal["gemini", "calendar", "twilio_sms", "twilio_numbers", "tts", "stt"]
Fault = Literal["timeout", "rate_limited", "server_error"]

PROVIDERS: tuple[Provider, ...] = (
    "gemini",
    "calendar",
    "twilio_sms",
    "twilio_numbers",
    "tts",
    "stt",
)
FAULTS: tuple[Fault, ...] = ("timeout", "rate_limited", "server_error")

CANARY = "PROVIDER-CANARY-5e1d9c"

# The port methods that make a provider attempt, per provider (what a proxy intercepts).
_OPS: dict[Provider, frozenset[str]] = {
    "gemini": frozenset({"generate"}),
    "calendar": frozenset(
        {
            "exchange_code",
            "refresh_access_token",
            "list_calendars",
            "free_busy",
            "insert_event",
            "delete_event",
            "revoke",
        }
    ),
    "twilio_sms": frozenset({"send_sms"}),
    "twilio_numbers": frozenset({"account", "list_numbers", "get_number", "set_webhooks"}),
    "tts": frozenset({"synthesize"}),
    "stt": frozenset({"transcribe"}),
}

ErrorFactory = Callable[[float | None], Exception]


def _gemini(reason: str, *, rate_limited: bool = False) -> ErrorFactory:
    return lambda retry_after: LLMUnavailableError(
        f"Gemini call failed ({reason}).",
        retryable=True,
        rate_limited=rate_limited,
        retry_after_seconds=retry_after if rate_limited else None,
    )


def _tts(reason: str, *, rate_limited: bool = False) -> ErrorFactory:
    return lambda _: TtsUnavailableError(
        f"Gemini TTS call failed ({reason}).", retryable=True, rate_limited=rate_limited
    )


def _stt(reason: str, *, rate_limited: bool = False) -> ErrorFactory:
    return lambda _: SttUnavailableError(
        f"Gemini STT call failed ({reason}).", retryable=True, rate_limited=rate_limited
    )


# One row per (provider, fault): the exception the real adapter raises for that outcome.
# Each comment names the adapter code that decides it.
_ERRORS: dict[tuple[Provider, Fault], ErrorFactory] = {
    # integrations/llm/gemini.py `GeminiClient.generate` + `_is_transient`: timeouts, 408,
    # 429 and 5xx are retryable; only a 429 is `rate_limited` (with Google's RetryInfo delay).
    ("gemini", "timeout"): _gemini("TimeoutError"),
    ("gemini", "rate_limited"): _gemini("ClientError", rate_limited=True),
    ("gemini", "server_error"): _gemini("ServerError"),
    # integrations/google_calendar/client.py `_send` (timeout) and `_error_for` (429, 5xx).
    ("calendar", "timeout"): lambda _: GoogleApiError("timeout", retryable=True),
    ("calendar", "rate_limited"): lambda _: GoogleApiError("rate_limited", retryable=True),
    ("calendar", "server_error"): lambda _: GoogleApiError("server_error", retryable=True),
    # integrations/twilio/client.py `HttpTwilioSmsApi.send_sms` + `_error_for`: Twilio's
    # Messages API has no idempotency key, so a timeout or 5xx AFTER the request was sent
    # is `send_unconfirmed` and NEVER retryable (a retry could text the owner twice); only
    # a 429 (nothing created) and connect errors are retryable.
    ("twilio_sms", "timeout"): lambda _: TwilioApiError("send_unconfirmed"),
    ("twilio_sms", "rate_limited"): lambda _: TwilioApiError("rate_limited", retryable=True),
    ("twilio_sms", "server_error"): lambda _: TwilioApiError("send_unconfirmed"),
    # integrations/twilio/numbers.py `HttpTwilioNumbersApi._request` + `_error_for`: these
    # reads and webhook updates are idempotent, so timeouts and 5xx ARE retryable.
    ("twilio_numbers", "timeout"): lambda _: TwilioApiError("transport", retryable=True),
    ("twilio_numbers", "rate_limited"): lambda _: TwilioApiError("rate_limited", retryable=True),
    ("twilio_numbers", "server_error"): lambda _: TwilioApiError("provider_error", retryable=True),
    # integrations/tts/gemini.py `GeminiTtsClient.synthesize` + `_is_transient` (no side
    # effect, so everything transient is retryable).
    ("tts", "timeout"): _tts("TimeoutError"),
    ("tts", "rate_limited"): _tts("ClientError", rate_limited=True),
    ("tts", "server_error"): _tts("ServerError"),
    # integrations/stt/gemini.py `GeminiSpeechToText.transcribe` + `_is_transient`.
    ("stt", "timeout"): _stt("TimeoutError"),
    ("stt", "rate_limited"): _stt("ClientError", rate_limited=True),
    ("stt", "server_error"): _stt("ServerError"),
}


@dataclass
class _Rule:
    fault: Fault
    remaining: int | None  # None = every call until healed
    op: str | None
    lands: bool
    retry_after_seconds: float | None


@dataclass
class ProviderChaos:
    canary: str = CANARY
    # Every delay `retry_bounded` was asked to sleep (the `chaos` fixture records instead of
    # sleeping), so a test can assert a provider's retry-after never became a real wait.
    sleeps: list[float] = field(default_factory=list)
    _rules: dict[Provider, _Rule] = field(default_factory=dict)
    _log: list[tuple[Provider, str]] = field(default_factory=list)

    def error_for(
        self, provider: Provider, fault: Fault, retry_after_seconds: float | None = None
    ) -> Exception:
        error = _ERRORS[provider, fault](retry_after_seconds)
        error.__cause__ = RuntimeError(self.canary)
        return error

    def fail(
        self,
        provider: Provider,
        fault: Fault,
        *,
        times: int | None = None,
        op: str | None = None,
        lands: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        """Make the next `times` attempts at `provider` (only its port method `op`, when
        given) fail with `fault`; `times=None` fails every attempt until `heal`."""
        if op is not None and op not in _OPS[provider]:
            raise ValueError(f"{op!r} is not a {provider} operation.")
        self._rules[provider] = _Rule(fault, times, op, lands, retry_after_seconds)

    def heal(self, provider: Provider | None = None) -> None:
        if provider is None:
            self._rules.clear()
        else:
            self._rules.pop(provider, None)

    def calls(self, provider: Provider, op: str | None = None) -> int:
        """Attempts that reached `provider` (injected failures included)."""
        return sum(1 for p, o in self._log if p == provider and op in (None, o))

    def wrap(self, provider: Provider, inner: Any) -> Any:
        """A stand-in for `inner` whose port methods go through this chaos."""
        return _ChaosProxy(self, provider, inner)

    def _begin(self, provider: Provider, op: str) -> _Rule | None:
        self._log.append((provider, op))
        rule = self._rules.get(provider)
        if rule is None or (rule.op is not None and rule.op != op):
            return None
        if rule.remaining is not None:
            if rule.remaining <= 0:
                return None
            rule.remaining -= 1
        return rule


class _ChaosProxy:
    def __init__(self, chaos: ProviderChaos, provider: Provider, inner: Any) -> None:
        self._chaos: ProviderChaos = chaos
        self._provider: Provider = provider
        self._inner: Any = inner

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name not in _OPS[self._provider]:
            return attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            rule = self._chaos._begin(self._provider, name)
            if rule is not None and not rule.lands:
                raise self._error(rule)
            result = await attr(*args, **kwargs)
            if rule is not None:  # lands: the work happened, the answer was lost
                raise self._error(rule)
            return result

        return call

    def _error(self, rule: _Rule) -> Exception:
        return self._chaos.error_for(self._provider, rule.fault, rule.retry_after_seconds)
