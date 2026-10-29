import pytest
from pydantic import ValidationError

from core.config import Settings

# The Stripe fields every successful Settings() needs; not under test here.
STRIPE = {
    "stripe_secret_key": "sk_test_placeholder",
    "stripe_webhook_secret": "whsec_placeholder",
    "stripe_price_id": "price_placeholder",
    "app_base_url": "http://localhost:3000",
}


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
        **STRIPE,
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
        cors_origins="https://a.example,https://b.example",
    )

    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_cors_origins_defaults_to_empty_list(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **STRIPE,
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
    )

    assert settings.cors_origins == []


def test_clerk_authorized_parties_splits_comma_separated_string():
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **STRIPE,
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
        clerk_authorized_parties="https://a.example,https://b.example",
    )

    assert settings.clerk_authorized_parties == ["https://a.example", "https://b.example"]


def test_clerk_authorized_parties_defaults_to_empty_list(monkeypatch):
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)

    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **STRIPE,
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
    )

    assert settings.clerk_authorized_parties == []


def test_clerk_jwks_url_is_derived_from_issuer():
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **STRIPE,
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev/",
    )

    assert settings.clerk_jwks_url == "https://example.clerk.accounts.dev/.well-known/jwks.json"


def _settings(**overrides):
    values = {
        "database_url": "postgresql://user:pass@localhost/db",
        "clerk_issuer": "https://example.clerk.accounts.dev",
        **STRIPE,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def test_live_stripe_key_is_rejected_outside_production():
    with pytest.raises(ValidationError, match="live Stripe key"):
        _settings(stripe_secret_key="sk_live_abc", environment="local")


def test_live_stripe_key_is_allowed_in_production():
    settings = _settings(
        stripe_secret_key="sk_live_abc",
        environment="production",
        gemini_api_key="test-key",
        clerk_secret_key="sk_live_abc",
    )
    assert settings.environment == "production"


def test_gemini_key_is_required_in_production():
    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        _settings(environment="production", clerk_secret_key="sk_live_abc")


def test_clerk_secret_key_is_required_in_production():
    # Without it no account deletion can remove the sign-in, which the privacy policy promises.
    with pytest.raises(ValidationError, match="CLERK_SECRET_KEY"):
        _settings(environment="production", gemini_api_key="test-key")


def test_clerk_secret_key_is_optional_outside_production():
    assert _settings(environment="local").clerk_secret_key is None


def test_gemini_key_is_optional_outside_production():
    assert _settings(environment="local").gemini_api_key is None


def test_non_stripe_secret_key_is_rejected():
    with pytest.raises(ValidationError, match="STRIPE_SECRET_KEY"):
        _settings(stripe_secret_key="pk_test_publishable")


def test_app_base_url_drops_trailing_slash():
    assert _settings(app_base_url="https://tether.example/").app_base_url == (
        "https://tether.example"
    )


@pytest.mark.parametrize("secret", ["", "whsec_", "sk_test_wrong_kind", "not-a-secret"])
def test_webhook_secret_must_be_a_stripe_signing_secret(secret):
    with pytest.raises(ValidationError, match="STRIPE_WEBHOOK_SECRET"):
        _settings(stripe_webhook_secret=secret)


def test_clerk_secret_key_is_optional_and_blank_means_unset():
    assert _settings().clerk_secret_key is None
    assert _settings(clerk_secret_key="  ").clerk_secret_key is None


def test_clerk_secret_key_is_kept_secret():
    settings = _settings(clerk_secret_key="sk_test_abc123")

    assert settings.clerk_secret_key is not None
    assert settings.clerk_secret_key.get_secret_value() == "sk_test_abc123"
    assert "abc123" not in repr(settings)


@pytest.mark.parametrize("value", ["pk_test_abc", "sk_test_", "sk_test_a b", "not-a-key"])
def test_clerk_secret_key_must_look_like_a_clerk_secret(value):
    with pytest.raises(ValidationError, match="CLERK_SECRET_KEY"):
        _settings(clerk_secret_key=value)


def test_live_clerk_key_is_rejected_outside_production():
    with pytest.raises(ValidationError, match="live Clerk key"):
        _settings(clerk_secret_key="sk_live_abc", environment="local")


def test_live_clerk_key_is_allowed_in_production():
    settings = _settings(
        clerk_secret_key="sk_live_abc", environment="production", gemini_api_key="test-key"
    )
    assert settings.clerk_secret_key is not None


def test_e2e_fake_providers_are_off_by_default():
    assert _settings().e2e_fake_providers is False


def test_e2e_fake_providers_are_allowed_locally():
    assert _settings(environment="local", e2e_fake_providers=True).e2e_fake_providers is True


def test_e2e_fake_providers_are_refused_against_a_remote_database():
    with pytest.raises(ValidationError, match="local DATABASE_URL"):
        _settings(
            environment="local",
            database_url="postgresql://u:p@ep-dev-123.neon.tech/db",
            e2e_fake_providers=True,
        )


def test_e2e_fake_providers_are_refused_in_production():
    # Everything else is valid for production, so only the fake-provider flag can fail.
    with pytest.raises(ValidationError, match="E2E_FAKE_PROVIDERS"):
        _settings(
            environment="production",
            gemini_api_key="test-key",
            clerk_secret_key="sk_live_abc",
            e2e_fake_providers=True,
        )
