"""In-memory `services.speech.AudioStore` for speech and call-session unit tests.

Expiry is not modelled (the Postgres `MediaStore` has its own tests); tokens and cache
entries are scoped exactly like the real store: a cache entry belongs to one business.
"""

import secrets
import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stored:
    content_type: str
    data: bytes
    ttl_seconds: int


@dataclass
class MemoryAudioStore:
    objects: dict[str, Stored] = field(default_factory=dict)  # token -> object
    cache: dict[tuple[uuid.UUID, str], Stored] = field(default_factory=dict)
    puts: int = 0
    fail_puts: bool = False

    async def put(
        self,
        business_id: uuid.UUID,
        data: bytes,
        content_type: str,
        *,
        ttl_seconds: int,
        cache_key: str | None = None,
    ) -> str:
        if self.fail_puts:
            raise ConnectionError("store down")
        self.puts += 1
        stored = Stored(content_type, data, ttl_seconds)
        if cache_key is not None:
            self.cache[(business_id, cache_key)] = stored
        token = secrets.token_urlsafe(32)
        self.objects[token] = stored
        return token

    async def cached(self, business_id: uuid.UUID, cache_key: str) -> str | None:
        stored = self.cache.get((business_id, cache_key))
        if stored is None:
            return None
        token = secrets.token_urlsafe(32)
        self.objects[token] = stored
        return token

    async def get(self, token: str) -> Stored | None:
        return self.objects.get(token)
