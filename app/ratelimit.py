"""Per-IP rate limiting for the expensive endpoints, hosted mode only.

In-memory sliding window. On a serverless platform every instance has its own memory, so this is best-effort: a
visitor spread across instances (or hitting a cold start) can get a few extra requests. It stops casual abuse of a
public demo; for exact limits you would need a shared store."""
from __future__ import annotations

import hmac
import math
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request

from .config import get_settings

_MAX_KEYS = 10_000          # bound memory: past this, expired entries are dropped before adding a new one

LABELS = {"analyze": "game analyses", "evaluate": "position evaluations", "coach": "coach messages"}


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}

    def hit(self, group: str, ip: str, limit: int, window: float) -> Optional[int]:
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
        wait = request.app.state.limiter.hit(group, client_ip(request), limit, s.hosted_rate_window)
        if wait is not None:
            mins = max(1, round(wait / 60))
            raise HTTPException(
                429, f"Rate limit reached: this demo allows {limit} {LABELS[group]} per "
                     f"{'hour' if s.hosted_rate_window == 3600 else f'{int(s.hosted_rate_window)} seconds'} per visitor. "
                     f"Try again in about {mins} minute{'s' if mins != 1 else ''}, or run Chessy locally for no limits.",
                headers={"Retry-After": str(wait)})
    return dep
