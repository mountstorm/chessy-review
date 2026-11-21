"""Position-evaluation cache keyed by (position, depth, multipv). Memory LRU by default,
Redis if REDIS_URL is set; both store JSON so entries are interchangeable."""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Optional, Protocol


class Cache(Protocol):
    async def get(self, key: str) -> Optional[dict]: ...
    async def set(self, key: str, value: dict) -> None: ...
    async def close(self) -> None: ...


class MemoryCache:
    def __init__(self, max_items: int = 50_000):
        self._d: "OrderedDict[str, dict]" = OrderedDict()
        self._max = max_items
        self.hits = 0
        self.misses = 0

    async def get(self, key):
        v = self._d.get(key)
        if v is None:
            self.misses += 1
            return None
        self._d.move_to_end(key)
        self.hits += 1
        return v

    async def set(self, key, value):
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self._max:
            self._d.popitem(last=False)

    async def close(self):
        pass


class RedisCache:
    def __init__(self, url: str, ttl: int = 60 * 60 * 24 * 14):
        import redis.asyncio as redis   # optional dependency: pip install redis
        self._r = redis.from_url(url)
        self._ttl = ttl

    async def get(self, key):
        raw = await self._r.get(f"chessy:{key}")
        return json.loads(raw) if raw else None

    async def set(self, key, value):
        await self._r.set(f"chessy:{key}", json.dumps(value), ex=self._ttl)

    async def close(self):
        await self._r.aclose()


def make_cache(redis_url: Optional[str], max_items: int) -> Cache:
    if redis_url:
        try:
            return RedisCache(redis_url)
        except ImportError:
            pass
    return MemoryCache(max_items)
