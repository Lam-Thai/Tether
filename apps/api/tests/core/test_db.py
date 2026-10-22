"""`is_local_database`: the check tests and the eval harness use to refuse real databases."""

import pytest

from core.db import is_local_database


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://tether:tether@localhost:5432/tether",
        "postgresql://tether:tether@127.0.0.1:54474/tether",
        "postgresql+asyncpg://u:p@[::1]:5432/db",
        "postgresql://u:p@localhost/db?host=127.0.0.1",
        "postgresql://u:p@localhost/db?host=localhost:5433,127.0.0.1",
    ],
)
def test_local_urls_are_local(url):
    assert is_local_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@ep-x.us-east-2.aws.neon.tech/neondb?sslmode=require",
        # A local authority host can't hide a remote one in the query string.
        "postgresql://u:p@localhost/db?host=db.example.com",
        "postgresql://u:p@localhost/db?host=db.example.com:5432",
        "postgresql://u:p@localhost/db?host=127.0.0.1,db.example.com",
        "postgresql://u:p@localhost/db?hostaddr=10.0.0.5",
        "postgresql://u:p@localhost/db?host=localhost&host=db.example.com",
        # No host at all (a socket path): refused, fail safe.
        "postgresql://u:p@/db?host=/var/run/postgresql",
    ],
)
def test_remote_or_unknown_hosts_are_not_local(url):
    assert not is_local_database(url)
