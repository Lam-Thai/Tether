"""Which database hosts are this machine. No settings import, so `core.config` can use
it (the E2E fake-provider guard) as well as `core.db`, the tests and the eval harness."""

from sqlalchemy.engine import make_url

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

__all__ = ["LOCAL_DB_HOSTS", "is_local_database"]
