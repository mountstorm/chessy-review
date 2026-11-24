"""Per-IP rate limiting for the expensive endpoints, hosted mode only.

Two limiters with the same `await hit(group, ip, limit, window)` contract:

* `RateLimiter`: in-memory sliding window. On a serverless platform every instance has its own memory, so this is
  best-effort: a visitor spread across instances (or hitting a cold start) gets a few extra requests. It is what runs
  locally and whenever no shared store is configured.
* `SharedRateLimiter`: counts in a shared store (Upstash Redis over its REST API), so every instance sees the same
  count. Fixed window: one atomic INCR per request, so a visitor can burst up to 2x the limit across a window edge. If
  the store is unreachable it falls back to an in-memory `RateLimiter` rather than failing the request."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Protocol, Tuple

import httpx
from fastapi import HTTPException, Request

from .config import get_settings

log = logging.getLogger("chessy.ratelimit")

_MAX_KEYS = 10_000          # bound memory: past this, expired entries are dropped before adding a new one

LABELS = {"analyze": "game analyses", "evaluate": "position evaluations", "coach": "coach messages"}


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}

    async def hit(self, group: str, ip: str, limit: int, window: float) -> Optional[int]:
        """Record a request. Returns None if allowed, else the seconds until a slot frees up."""
        now = self._clock()
        key = (group, ip)
        q = self._hits.get(key)
        if q is None:
            if len(self._hits) >= _MAX_KEYS:
                self._prune(now, window)
            q = self._hits[key] = deque()
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            return max(1, math.ceil(window - (now - q[0])))
        q.append(now)
        return None

    def _prune(self, now: float, window: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] >= window]:
            del self._hits[k]


class StoreError(Exception):
    """The shared store could not be used (network, auth, or an unexpected reply)."""


class CounterStore(Protocol):
    async def incr(self, key: str, window: float) -> Tuple[int, int]:
        """Add one to `key` (starting a `window`-second expiry if it is new). Returns (count, seconds until it resets)."""


class UpstashStore:
    """Upstash Redis over REST: one pipelined round trip per request (INCR, EXPIRE ... NX, TTL)."""

    def __init__(self, url: str, token: str, timeout: float = 2.0, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._url, self._token, self._timeout, self._transport = url.rstrip("/"), token, timeout, transport
        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _http(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:          # a client is tied to the loop it was made in
            self._client, self._loop = httpx.AsyncClient(timeout=self._timeout, transport=self._transport), loop
        return self._client

    async def incr(self, key: str, window: float) -> Tuple[int, int]:
        body = [["INCR", key], ["EXPIRE", key, int(window), "NX"], ["TTL", key]]
        try:
            r = await self._http().post(f"{self._url}/pipeline", json=body, headers={"Authorization": f"Bearer {self._token}"})
            r.raise_for_status()
            res = r.json()
            if not isinstance(res, list) or len(res) != 3 or any("error" in x for x in res):
                raise StoreError("unexpected reply from the rate-limit store")
            count, ttl = int(res[0]["result"]), int(res[2]["result"])
        except StoreError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise StoreError(type(e).__name__) from None             # the class only: never echo URLs or tokens
        return count, ttl if ttl > 0 else int(window)


class SharedRateLimiter:
    def __init__(self, store: CounterStore, fallback: Optional[RateLimiter] = None):
        self._store = store
        self._fallback = fallback or RateLimiter()
        self._down = False

    @staticmethod
    def _key(group: str, ip: str) -> str:
        return f"chessy:rl:{group}:{hashlib.sha256(ip.encode()).hexdigest()[:16]}"     # no raw visitor IPs in a third-party store

    async def hit(self, group: str, ip: str, limit: int, window: float) -> Optional[int]:
        try:
            count, ttl = await self._store.incr(self._key(group, ip), window)
        except StoreError as e:
            if not self._down:
                log.warning("Rate-limit store unavailable (%s); limiting in memory per instance until it is back", e)
            self._down = True
            return await self._fallback.hit(group, ip, limit, window)
        if self._down:
            log.info("Rate-limit store is back")
            self._down = False
        return max(1, ttl) if count > limit else None


def make_limiter(settings) -> "RateLimiter | SharedRateLimiter":
    if settings.upstash_redis_rest_url and settings.upstash_redis_rest_token:
        return SharedRateLimiter(UpstashStore(settings.upstash_redis_rest_url, settings.upstash_redis_rest_token))
    return RateLimiter()


def client_ip(request: Request) -> str:
    """The caller's address. Behind the platform proxy `request.client` is the proxy itself, so hosted mode reads
    X-Forwarded-For, taking the LAST entry: that is the one the nearest proxy added, which a client can't forge
    (the first entries are whatever the client chose to send)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        last = fwd.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def rate_limit(group: str):
    """FastAPI dependency: 429 once an IP is over the hourly budget for `group`. No-op outside hosted mode, when the
    limit is 0, or for visitors who present the access code (the owner already trusts them)."""
    async def dep(request: Request) -> None:
        s = get_settings()
        limit = getattr(s, f"hosted_rate_{group}")
        if not s.hosted or limit <= 0:
            return
        if s.access_code and hmac.compare_digest(request.headers.get("x-access-code", ""), s.access_code):
            return
        wait = await request.app.state.limiter.hit(group, client_ip(request), limit, s.hosted_rate_window)
        if wait is not None:
            mins = max(1, round(wait / 60))
            raise HTTPException(
                429, f"Rate limit reached: this demo allows {limit} {LABELS[group]} per "
                     f"{'hour' if s.hosted_rate_window == 3600 else f'{int(s.hosted_rate_window)} seconds'} per visitor. "
                     f"Try again in about {mins} minute{'s' if mins != 1 else ''}, or run Chessy locally for no limits.",
                headers={"Retry-After": str(wait)})
    return dep
