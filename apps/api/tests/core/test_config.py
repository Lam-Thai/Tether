import pytest
from pydantic import ValidationError

from core.config import Settings


def test_missing_database_url_raises_clear_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, clerk_issuer="https://example.clerk.accounts.dev")  # pyright: ignore[reportCallIssue]

    assert "database_url" in str(exc_info.value).lower()


def test_missing_clerk_issuer_raises_clear_error(monkeypatch):
    monkeypatch.delenv("CLERK_ISSUER", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, database_url="postgresql://user:pass@localhost/db")  # pyright: ignore[reportCallIssue]

    assert "clerk_issuer" in str(exc_info.value).lower()


def test_cors_origins_splits_comma_separated_string():
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
        cors_origins="https://a.example,https://b.example",
    )

    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_cors_origins_defaults_to_empty_list(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
    )

    assert settings.cors_origins == []


def test_clerk_authorized_parties_splits_comma_separated_string():
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
        clerk_authorized_parties="https://a.example,https://b.example",
    )

    assert settings.clerk_authorized_parties == ["https://a.example", "https://b.example"]


def test_clerk_authorized_parties_defaults_to_empty_list(monkeypatch):
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)

    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
    )

    assert settings.clerk_authorized_parties == []


def test_clerk_jwks_url_is_derived_from_issuer():
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev/",
    )

    assert settings.clerk_jwks_url == "https://example.clerk.accounts.dev/.well-known/jwks.json"
