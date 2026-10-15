import pytest
from pydantic import ValidationError

from core.config import Settings


def test_missing_database_url_raises_clear_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert "database_url" in str(exc_info.value).lower()


def test_cors_origins_splits_comma_separated_string():
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql://user:pass@localhost/db",
        cors_origins="https://a.example,https://b.example",
    )

    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_cors_origins_defaults_to_empty_list(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql://user:pass@localhost/db",
    )

    assert settings.cors_origins == []
