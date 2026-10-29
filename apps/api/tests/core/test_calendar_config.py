"""Google Calendar settings: all optional, strict format when set."""

import pytest
from pydantic import ValidationError

from core.config import Settings, parse_token_keys

_KEY = "A" * 43  # 32 bytes in unpadded base64url
STRIPE = {
    "stripe_secret_key": "sk_test_placeholder",
    "stripe_webhook_secret": "whsec_placeholder",
    "stripe_price_id": "price_placeholder",
    "app_base_url": "http://localhost:3000",
}


def _settings(**calendar) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **STRIPE,
        database_url="postgresql://user:pass@localhost/db",
        clerk_issuer="https://example.clerk.accounts.dev",
        **calendar,
    )


def test_calendar_is_optional_and_unconfigured_by_default(monkeypatch):
    for name in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "CALENDAR_TOKEN_KEYS",
        "OAUTH_STATE_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    # Even in production, where only a missing Gemini or Clerk key may stop startup.
    settings = _settings(environment="production", gemini_api_key="k", clerk_secret_key="sk_test_k")

    assert settings.calendar_configured is False
    assert settings.calendar_redirect_uri == (
        "http://localhost:3000/dashboard/settings/calendar/callback"
    )
    assert (
        settings.calendar_oauth_state_ttl_seconds,
        settings.calendar_status_check_seconds,
        settings.google_calendar_timeout_seconds,
    ) == (600, 300, 10.0)


def test_calendar_is_configured_only_when_all_four_secrets_are_set():
    full = {
        "google_oauth_client_id": "id.apps.googleusercontent.com",
        "google_oauth_client_secret": "shh",
        "calendar_token_keys": f"k1:{_KEY}",
        "oauth_state_secret": "s" * 32,
    }
    assert _settings(**full).calendar_configured is True
    for missing in full:
        partial = {k: v for k, v in full.items() if k != missing}
        assert _settings(**partial).calendar_configured is False
    # A blank value in a .env file means unset.
    assert _settings(**{**full, "google_oauth_client_id": " "}).calendar_configured is False


def test_token_keys_first_is_active_and_secrets_are_masked():
    keys, active = parse_token_keys(f"new_1:{_KEY}, old-1:{'B' * 43}")

    assert active == "new_1"
    assert list(keys) == ["new_1", "old-1"]
    assert all(len(k) == 32 for k in keys.values())
    settings = _settings(calendar_token_keys=f"k1:{_KEY}", oauth_state_secret="s" * 32)
    assert _KEY not in repr(settings)


@pytest.mark.parametrize(
    "value",
    [
        "no-colon",
        f"UPPER:{_KEY}",
        f":{_KEY}",
        f"{'a' * 33}:{_KEY}",
        "k1:short",
        f"k1:{'A' * 44}",
        f"k1:{_KEY},k1:{_KEY}",
        f"k1:{_KEY},",
        "k1:!!!!",
    ],
    ids=[
        "no-colon",
        "uppercase-id",
        "empty-id",
        "long-id",
        "short-key",
        "long-key",
        "duplicate-id",
        "trailing-comma",
        "not-base64",
    ],
)
def test_bad_token_keys_fail_at_startup_without_echoing_the_key(value):
    with pytest.raises(ValidationError) as caught:
        _settings(calendar_token_keys=value)
    assert _KEY not in str(caught.value)


def test_state_secret_must_be_long_and_numbers_are_bounded():
    with pytest.raises(ValidationError):
        _settings(oauth_state_secret="short")
    for bad in (
        {"calendar_oauth_state_ttl_seconds": 59},
        {"calendar_oauth_state_ttl_seconds": 3601},
        {"calendar_status_check_seconds": -1},
        {"google_calendar_timeout_seconds": 0},
        {"google_calendar_timeout_seconds": 31},
    ):
        with pytest.raises(ValidationError):
            _settings(**bad)
