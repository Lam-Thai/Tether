"""Application settings.

This module is the *only* place anywhere in the app that reads environment
variables. Every other layer must import `settings` from here instead of
touching `os.environ` directly.
"""

import json
from typing import Annotated, Literal

from pydantic import field_validator
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
