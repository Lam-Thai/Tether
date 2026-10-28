"""In-memory `MediaRepository` with the same contract as `SqlMediaRepository`."""

import uuid
from dataclasses import dataclass

from services.media import MediaObject


@dataclass
class _Obj:
    business_id: uuid.UUID
    content_type: str
    data: bytes
    cache_key: str | None
    expires_at: int


class MemoryMediaRepo:
    def __init__(self) -> None:
        self.now = 0  # seconds; tests advance it
        self.objects: dict[uuid.UUID, _Obj] = {}
        self.tokens: dict[bytes, tuple[uuid.UUID, int]] = {}
        self.calls: list[str] = []
        self.fail_purge = False

    async def store(
        self,
        *,
        business_id,
        data,
        content_type,
        cache_key,
        object_ttl_seconds,
        token_hash,
        token_ttl_seconds,
    ) -> None:
        self.calls.append("store")
        object_id = None
        if cache_key is not None:
            object_id = next(
                (
                    i
                    for i, o in self.objects.items()
                    if o.business_id == business_id and o.cache_key == cache_key
                ),
                None,
            )
        if object_id is None:
            object_id = uuid.uuid4()
        self.objects[object_id] = _Obj(
            business_id, content_type, data, cache_key, self.now + object_ttl_seconds
        )
        self.tokens[token_hash] = (object_id, self.now + token_ttl_seconds)

    async def mint_for_cached(self, *, business_id, cache_key, token_hash, token_ttl_seconds):
        self.calls.append("mint_for_cached")
        for object_id, obj in self.objects.items():
            if (
                obj.business_id == business_id
                and obj.cache_key == cache_key
                and obj.expires_at > self.now
            ):
                self.tokens[token_hash] = (object_id, self.now + token_ttl_seconds)
                return True
        return False

    async def get_live(self, token_hash) -> MediaObject | None:
        self.calls.append("get_live")
        found = self.tokens.get(token_hash)
        if found is None or found[1] <= self.now:
            return None
        obj = self.objects.get(found[0])
        if obj is None or obj.expires_at <= self.now:
            return None
        return MediaObject(content_type=obj.content_type, data=obj.data)

    async def purge_expired(self, *, limit: int) -> int:
        self.calls.append("purge_expired")
        if self.fail_purge:
            raise RuntimeError("boom secret-token")
        dead = [h for h, (_, exp) in self.tokens.items() if exp <= self.now][:limit]
        for h in dead:
            del self.tokens[h]
        gone = [i for i, o in self.objects.items() if o.expires_at <= self.now][:limit]
        for i in gone:
            del self.objects[i]
            for h in [h for h, (oid, _) in self.tokens.items() if oid == i]:
                del self.tokens[h]
        return len(dead) + len(gone)
