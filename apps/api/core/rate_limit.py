"""Rate-limit wiring (slice 17): settings -> rules, the process's `RateLimiter`, and the
FastAPI dependencies that routers attach.

`SqlRateLimitStore` binds a session *factory*: every call is its own committed
transaction, so a hit is visible to every instance at once and nothing is held open
across the request. Tests override `get_rate_limiter` (an in-memory store) and
`get_rate_rules` (tightened limits).

Dependencies (the coverage test in `tests/api/test_rate_limit.py` fails for a mutating
route that carries none of them):
- `limit_tenant_mutations`: every POST/PUT/PATCH/DELETE of a signed-in owner, per business
  (per user before onboarding). Safe methods pass through. Attached per router in
  `api/v1/router.py`.
- `limit_agent_test`, `limit_voice_preview`: stricter per-business rules on the routes
  that spend Gemini requests.
- `limit_webhook`: per client IP, BEFORE the signature check so a flood is cut cheaply.
  A 429 is the answer even on Twilio voice URLs (Twilio plays its generic error): it takes
  more than `RATE_LIMIT_WEBHOOKS_PER_MINUTE` requests a minute from one address, far above
  real traffic, so a polite TwiML refusal isn't worth a second code path.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings, settings
from core.db import async_session_factory
from core.tenant import Tenant, get_tenant
from repositories import rate_limit as rate_limit_repo
from services.rate_limit import RateLimitedError, RateLimiter, RateRule

logger = logging.getLogger("tether.rate_limit")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WINDOW_SECONDS = 60
UNKNOWN_CLIENT = "unknown"
MAX_IP_LENGTH = 64


@dataclass(frozen=True)
class RateRules:
    enabled: bool
    agent_test: RateRule
    voice_preview: RateRule
    mutations: RateRule
    webhooks: RateRule


def rate_rules(config: Settings = settings) -> RateRules:
    def rule(name: str, limit: int) -> RateRule:
        return RateRule(name, limit, WINDOW_SECONDS)

    return RateRules(
        enabled=config.rate_limit_enabled,
        agent_test=rule("agent_test", config.rate_limit_agent_test_per_minute),
        voice_preview=rule("voice_preview", config.rate_limit_voice_preview_per_minute),
        mutations=rule("mutations", config.rate_limit_mutations_per_minute),
        webhooks=rule("webhooks", config.rate_limit_webhooks_per_minute),
    )


class SqlRateLimitStore:
    """`services.rate_limit.RateLimitStore` over Postgres; one committed transaction per call."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def increment(self, key: str, window_start: datetime, limit: int) -> bool:
        async with self._sessions() as session:
            counted = await rate_limit_repo.increment(session, key, window_start, limit)
            await session.commit()
            return counted

    async def purge(self, before: datetime) -> None:
        async with self._sessions() as session:
            await rate_limit_repo.purge_before(session, before)
            await session.commit()


# One limiter per process: it holds a session factory, never counts.
_limiter = RateLimiter(SqlRateLimitStore(async_session_factory))


def get_rate_limiter() -> RateLimiter:
    return _limiter


def get_rate_rules() -> RateRules:
    return rate_rules()


def client_ip(request: Request) -> str:
    """The caller's address: the configured trusted proxy header, else the socket peer."""
    header = settings.trusted_client_ip_header
    if header:
        # The LAST hop: the one our trusted proxy appended. Earlier entries of a
        # forwarded-for list are whatever the client sent, so trusting the first would let
        # a caller pick a fresh key per request. (Vercel's `x-real-ip` is a single value.)
        forwarded = request.headers.get(header, "").split(",")[-1].strip()
        if forwarded:
            return forwarded[:MAX_IP_LENGTH]
    return (request.client.host if request.client else UNKNOWN_CLIENT)[:MAX_IP_LENGTH]


def warn_if_client_ip_unknown(config: Settings = settings) -> bool:
    """Called at startup. Behind a proxy (Vercel) the socket peer is the proxy, so with no
    `TRUSTED_CLIENT_IP_HEADER` every webhook caller shares ONE budget and a flood from
    anyone could refuse Twilio's real requests. Warns (returns True) in production."""
    if config.environment != "production" or not config.rate_limit_enabled:
        return False
    if config.trusted_client_ip_header:
        return False
    logger.warning("rate_limit_client_ip_header_unset")
    return True


def _owner(tenant: Tenant) -> str:
    return f"business:{tenant.business.id}" if tenant.business else f"user:{tenant.user.id}"


async def _enforce(limiter: RateLimiter, rule: RateRule, subject: str) -> None:
    decision = await limiter.hit(rule, subject)
    if not decision.allowed:
        raise RateLimitedError(decision.retry_after_seconds)


async def limit_tenant_mutations(
    request: Request,
    tenant: Tenant = Depends(get_tenant),
    limiter: RateLimiter = Depends(get_rate_limiter),
    rules: RateRules = Depends(get_rate_rules),
) -> None:
    if rules.enabled and request.method not in SAFE_METHODS:
        await _enforce(limiter, rules.mutations, _owner(tenant))


async def limit_agent_test(
    tenant: Tenant = Depends(get_tenant),
    limiter: RateLimiter = Depends(get_rate_limiter),
    rules: RateRules = Depends(get_rate_rules),
) -> None:
    if rules.enabled:
        await _enforce(limiter, rules.agent_test, _owner(tenant))


async def limit_voice_preview(
    tenant: Tenant = Depends(get_tenant),
    limiter: RateLimiter = Depends(get_rate_limiter),
    rules: RateRules = Depends(get_rate_rules),
) -> None:
    if rules.enabled:
        await _enforce(limiter, rules.voice_preview, _owner(tenant))


async def limit_webhook(
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter),
    rules: RateRules = Depends(get_rate_rules),
) -> None:
    if rules.enabled:
        await _enforce(limiter, rules.webhooks, client_ip(request))


# What the coverage test looks for on a mutating route.
RATE_LIMIT_DEPENDENCIES = frozenset(
    {limit_tenant_mutations, limit_agent_test, limit_voice_preview, limit_webhook}
)
