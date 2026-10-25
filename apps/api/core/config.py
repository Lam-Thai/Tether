"""Application settings.

This module is the *only* place anywhere in the app that reads environment
variables. Every other layer must import `settings` from here instead of
touching `os.environ` directly.
"""

import base64
import binascii
import json
import os
import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# `TETHER_ENV_FILE=` (empty) reads no env file at all: `scripts/test.sh` sets it so tests
# see only the CI-like settings it exports, never a developer's .env (real keys, a remote
# database). Unset, the app reads `.env` as before.
_ENV_FILE = os.environ.get("TETHER_ENV_FILE", ".env") or None


class Settings(BaseSettings):
    # `hide_input_in_errors`: a bad value (a malformed token key, a database URL with a
    # password) must never be echoed back in the startup error.
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore", hide_input_in_errors=True)

    database_url: str
    # `NoDecode` stops pydantic-settings from eagerly JSON-decoding this env
    # value (and raising on anything that isn't valid JSON) before our own
    # "before" validator below gets a chance to handle the plain
    # comma-separated form Vercel/CI env vars typically use.
    cors_origins: Annotated[list[str], NoDecode] = []
    environment: Literal["local", "ci", "production"] = "local"
    app_version: str = "0.1.0"

    # Clerk session tokens are verified against this issuer's JWKS
    # (`{clerk_issuer}/.well-known/jwks.json`) — see `integrations/clerk/`.
    # It's a public URL, not a secret; the backend never needs Clerk's
    # secret key since RS256 verification only needs the public JWKS.
    clerk_issuer: str
    # Clerk's `azp` claim, restricted to the frontend origin(s) allowed to
    # mint tokens for this API. Empty means "don't check" (local dev).
    clerk_authorized_parties: Annotated[list[str], NoDecode] = []

    # Stripe (integrations/stripe). Test-mode keys only outside production.
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_price_id: str
    billing_trial_days: int = Field(default=14, ge=1, le=90)
    # Public origin of apps/web; Checkout and Portal return URLs hang off it.
    app_base_url: str

    # Gemini (integrations/llm). The key is optional so CI and local tests run on
    # the fake; production refuses to start without it. Never hardcode the model.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    # Per attempt; the adapter makes at most 2 attempts.
    gemini_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # Ceiling on all model work for one interaction summary (retry included); past
    # it the deterministic template is stored.
    summary_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    # How many recent messages each turn sends to the model (PRD §9.1).
    conversation_history_limit: int = Field(default=20, ge=2, le=100)
    # Hard ceiling on all model work in one turn (invalid-output retry included).
    # Past it the turn answers with the provider fallback. Keep it well under the
    # web client's turn timeout and the API's serverless function limit.
    conversation_turn_timeout_seconds: float = Field(default=25.0, gt=0, le=120)

    # Most FAQs one business may keep (PRD §8.4); creating more is 409 faq_limit_reached.
    faq_max_per_business: int = Field(default=200, ge=1, le=1000)
    service_max_per_business: int = Field(default=100, ge=1, le=1000)

    # Default booking rules (slice 10); a business overrides them in `booking_rules`.
    # Bounds match the CHECK constraints on that table.
    booking_slot_interval_minutes: int = Field(default=15, ge=5, le=240, multiple_of=5)
    booking_min_notice_minutes: int = Field(default=120, ge=0, le=20160)
    booking_max_days_ahead: int = Field(default=60, ge=1, le=365)
    booking_buffer_minutes: int = Field(default=0, ge=0, le=240)

    # Usage limits (services/usage.py, PRD §14.5). "Per day" is the UTC day; a
    # global cap covers all businesses together. 0 blocks the feature entirely.
    gemini_requests_per_day: int = Field(default=200, ge=0)
    gemini_requests_per_business_per_day: int = Field(default=50, ge=0)
    tts_requests_per_day: int = Field(default=50, ge=0)
    tts_requests_per_business_per_day: int = Field(default=10, ge=0)
    calls_per_day: int = Field(default=50, ge=0)
    calls_per_business_per_day: int = Field(default=10, ge=0)
    sms_per_day: int = Field(default=50, ge=0)
    sms_per_business_per_day: int = Field(default=10, ge=0)
    max_call_duration_seconds: int = Field(default=600, ge=30, le=3600)
    max_concurrent_interactions: int = Field(default=20, ge=0)
    max_concurrent_interactions_per_business: int = Field(default=5, ge=0)
    # An active interaction idle this long stops counting toward concurrency.
    interaction_idle_minutes: int = Field(default=15, ge=1, le=240)
    # Estimated cost only (free tier may bill nothing). Defaults: Gemini 2.5
    # Flash paid-tier list price; update alongside GEMINI_MODEL.
    gemini_input_usd_per_million_tokens: Decimal = Field(default=Decimal("0.30"), ge=0)
    gemini_output_usd_per_million_tokens: Decimal = Field(default=Decimal("2.50"), ge=0)

    # Google Calendar (integrations/google_calendar). Every value is optional so the app
    # starts without them; the feature is "configured" only when all four secrets below
    # are set, and until then every calendar endpoint but GET /status answers 503
    # `calendar_not_configured`. Generate keys with `uv run python -m core.keygen`.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    # Comma-separated `key_id:base64url(32 bytes)`; the FIRST key seals new tokens, the
    # rest only decrypt (rotation).
    calendar_token_keys: SecretStr | None = None
    # HMAC secret for the OAuth `state` JWT and the PKCE verifier derivation.
    oauth_state_secret: SecretStr | None = None
    calendar_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    # A stored connection is probed against Google when it was last confirmed longer ago.
    calendar_status_check_seconds: int = Field(default=300, ge=0, le=86400)
    # Per attempt; calls retry at most once (services/calendar.CALENDAR_RETRY).
    google_calendar_timeout_seconds: float = Field(default=10.0, gt=0, le=30)

    # Eval harness (`uv run tether-eval`, evals/runner.py). The tier picks the defaults
    # in `evals.runner.TIER_LIMITS`; each value below overrides one of them when set.
    eval_tier: Literal["free", "paid"] = "free"
    eval_max_requests: int | None = Field(default=None, ge=1, le=5000)
    eval_requests_per_minute: float | None = Field(default=None, gt=0, le=2000)
    eval_concurrency: int | None = Field(default=None, ge=1, le=16)
    # Tests refuse a non-local DATABASE_URL unless this is set (tests/conftest.py).
    # Only for a disposable test database: tests create and delete tenants.
    test_database_allow_remote: bool = False

    @field_validator("stripe_secret_key")
    @classmethod
    def _secret_key_is_a_stripe_key(cls, value: str) -> str:
        if not value.startswith(("sk_", "rk_")):
            raise ValueError("STRIPE_SECRET_KEY must be a Stripe secret or restricted key.")
        return value

    @field_validator("stripe_webhook_secret")
    @classmethod
    def _webhook_secret_is_a_signing_secret(cls, value: str) -> str:
        # An empty secret would make HMAC verification forgeable by anyone.
        if not value.startswith("whsec_") or len(value) <= len("whsec_"):
            raise ValueError("STRIPE_WEBHOOK_SECRET must be a Stripe signing secret (whsec_...).")
        return value

    @model_validator(mode="after")
    def _live_stripe_keys_only_in_production(self) -> "Settings":
        is_live = self.stripe_secret_key.startswith(("sk_live_", "rk_live_"))
        if is_live and self.environment != "production":
            raise ValueError("A live Stripe key is only allowed when ENVIRONMENT=production.")
        return self

    @model_validator(mode="after")
    def _gemini_key_required_in_production(self) -> "Settings":
        if self.environment == "production" and not (
            self.gemini_api_key and self.gemini_api_key.get_secret_value()
        ):
            raise ValueError("GEMINI_API_KEY is required when ENVIRONMENT=production.")
        return self

    @field_validator(
        "google_oauth_client_id",
        "google_oauth_client_secret",
        "calendar_token_keys",
        "oauth_state_secret",
        mode="before",
    )
    @classmethod
    def _blank_calendar_setting_is_unset(cls, value: object) -> object:
        # `GOOGLE_OAUTH_CLIENT_ID=` in a .env means "not configured", not an empty value.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("calendar_token_keys")
    @classmethod
    def _token_keys_are_well_formed(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            parse_token_keys(value.get_secret_value())  # raises ValueError on a bad format
        return value

    @field_validator("oauth_state_secret")
    @classmethod
    def _state_secret_is_long_enough(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("OAUTH_STATE_SECRET must be at least 32 characters.")
        return value

    @field_validator("app_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a JSON array string or a comma-separated string from env."""
        return _split_csv_or_json(value)

    @field_validator("clerk_authorized_parties", mode="before")
    @classmethod
    def _split_clerk_authorized_parties(cls, value: object) -> object:
        return _split_csv_or_json(value)

    @property
    def calendar_redirect_uri(self) -> str:
        """Where Google sends the owner back: a page of the web app, never the API."""
        return f"{self.app_base_url}/dashboard/settings/calendar/callback"

    @property
    def calendar_configured(self) -> bool:
        return all(
            (
                self.google_oauth_client_id,
                self.google_oauth_client_secret,
                self.calendar_token_keys,
                self.oauth_state_secret,
            )
        )

    @property
    def clerk_jwks_url(self) -> str:
        return f"{self.clerk_issuer.rstrip('/')}/.well-known/jwks.json"


_KEY_ID = re.compile(r"[a-z0-9_-]{1,32}")


def parse_token_keys(raw: str) -> tuple[dict[str, bytes], str]:
    """Parses `id:base64url,id2:base64url` into (keys, active key id = the first).

    Errors name the position, never the key material."""
    keys: dict[str, bytes] = {}
    active = ""
    for position, entry in enumerate(raw.split(","), start=1):
        key_id, sep, encoded = entry.strip().partition(":")
        if not sep or not _KEY_ID.fullmatch(key_id):
            raise ValueError(
                f"CALENDAR_TOKEN_KEYS entry {position} must look like <id>:<base64url key>, "
                "with an id of 1-32 characters from a-z, 0-9, _ and -."
            )
        if key_id in keys:
            raise ValueError("CALENDAR_TOKEN_KEYS ids must be unique.")
        try:
            key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (binascii.Error, ValueError):
            key = b""
        if len(key) != 32:
            raise ValueError(f"CALENDAR_TOKEN_KEYS entry {position} must be 32 bytes, base64url.")
        keys[key_id] = key
        active = active or key_id
    return keys, active


def _split_csv_or_json(value: object) -> object:
    """Accept a JSON array string or a comma-separated string from env."""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            return json.loads(stripped)
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return value


settings = Settings()  # pyright: ignore[reportCallIssue]
