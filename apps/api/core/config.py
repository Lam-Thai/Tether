"""Application settings.

This module is the *only* place anywhere in the app that reads environment
variables. Every other layer must import `settings` from here instead of
touching `os.environ` directly.
"""

import json
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    # How many recent messages each turn sends to the model (PRD §9.1).
    conversation_history_limit: int = Field(default=20, ge=2, le=100)
    # Hard ceiling on all model work in one turn (invalid-output retry included).
    # Past it the turn answers with the provider fallback. Keep it well under the
    # web client's turn timeout and the API's serverless function limit.
    conversation_turn_timeout_seconds: float = Field(default=25.0, gt=0, le=120)

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
    def clerk_jwks_url(self) -> str:
        return f"{self.clerk_issuer.rstrip('/')}/.well-known/jwks.json"


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
