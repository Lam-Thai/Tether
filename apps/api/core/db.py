"""Async SQLAlchemy engine + session factory.

Hides serverless-safe connection handling so callers never think about
pooling: build one engine per process, one session per request.
"""

from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import settings

# libpq/psycopg-style query params that Neon's connection-string UI includes
# (`sslmode`, `channel_binding`) but that asyncpg's `connect()` doesn't
# accept as keyword arguments — SQLAlchemy's asyncpg dialect forwards every
# URL query param straight through, so left in place these raise
# `TypeError: connect() got an unexpected keyword argument 'sslmode'`.
_ASYNCPG_INCOMPATIBLE_QUERY_KEYS = {"sslmode", "channel_binding"}
_SSL_REQUIRING_SSLMODES = {"require", "verify-ca", "verify-full"}


def _normalize_database_url(raw_url: str) -> str:
    # Neon's connection-string UI hands out a plain `postgresql://` URL with
    # no driver specified; SQLAlchemy's async engine needs an explicit async
    # driver, so rewrite it to use asyncpg when one isn't already present.
    if raw_url.startswith("postgresql://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlsplit(raw_url)
    kept_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query)
        if key not in _ASYNCPG_INCOMPATIBLE_QUERY_KEYS
    ]
    return urlunsplit(parsed._replace(query=urlencode(kept_params)))


def _requires_ssl(raw_url: str) -> bool:
    """Translate the stripped `sslmode` into asyncpg's own `ssl` connect arg,
    so a managed Postgres that requires TLS (Neon) still gets it, while a
    plain local/CI Postgres (no `sslmode` at all) isn't forced into it.
    """
    sslmode = dict(parse_qsl(urlsplit(raw_url).query)).get("sslmode")
    return sslmode in _SSL_REQUIRING_SSLMODES


def _connect_args(raw_url: str) -> dict:
    connect_args: dict = {"server_settings": {"statement_timeout": "5000"}}
    if _requires_ssl(raw_url):
        connect_args["ssl"] = True
    return connect_args


# Hosts that are this machine. Tests and the eval harness refuse any other database
# unless explicitly allowed (tests/conftest.py, evals/cli.py).
LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


# Query parameters the driver may take a connection host from, overriding the URL's own.
_HOST_QUERY_KEYS = ("host", "hostaddr")


def is_local_database(raw_url: str) -> bool:
    """Whether every host this URL could connect to is this machine.

    asyncpg receives the URL's query parameters, so `?host=db.example.com` would
    override a local authority host: those count too. A URL with no host at all
    (a socket path) is not treated as local, so callers fail safe.
    """
    url = make_url(raw_url)
    hosts: list[str | None] = [url.host]
    for key in _HOST_QUERY_KEYS:
        value = url.query.get(key)
        values = value if isinstance(value, tuple) else (value,) if value else ()
        for entry in values:
            hosts.extend(_strip_port(part.strip()) for part in entry.split(","))
    return all(host in LOCAL_DB_HOSTS for host in hosts)


def _strip_port(host: str) -> str:
    if host.startswith("["):  # [::1]:5432
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:  # localhost:5432
        return host.split(":", 1)[0]
    return host


engine = create_async_engine(
    _normalize_database_url(settings.database_url),
    poolclass=NullPool,  # Neon's own pooler handles pooling at the proxy layer.
    pool_pre_ping=True,
    connect_args=_connect_args(settings.database_url),
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield one session per request, close it after."""
    async with async_session_factory() as session:
        yield session
