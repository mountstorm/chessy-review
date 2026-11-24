"""Per-IP rate limiting in hosted mode."""
import asyncio
import json
import logging
from types import SimpleNamespace

import httpx

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.config import get_settings
from app.main import create_app
from app.ratelimit import RateLimiter, SharedRateLimiter, StoreError, UpstashStore, client_ip, make_limiter, rate_limit
from conftest import needs_engine

SCHOLAR = "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def hit(rl, *a):
    return asyncio.run(rl.hit(*a))


# ---------------------------------------------------------------- the limiter itself
def test_allows_up_to_the_limit_then_blocks_with_a_retry_time():
    clock = Clock()
    rl = RateLimiter(clock)
    assert [hit(rl, "analyze", "1.2.3.4", 3, 3600) for _ in range(3)] == [None, None, None]
    clock.t += 600
    assert hit(rl, "analyze", "1.2.3.4", 3, 3600) == 3000          # oldest hit frees up 3000s from now


def test_window_slides_so_old_hits_stop_counting():
    clock = Clock()
    rl = RateLimiter(clock)
    for _ in range(2):
        hit(rl, "analyze", "ip", 2, 3600)
    assert hit(rl, "analyze", "ip", 2, 3600) is not None
    clock.t += 3600
    assert hit(rl, "analyze", "ip", 2, 3600) is None


def test_ips_and_groups_are_counted_separately():
    rl = RateLimiter(Clock())
    assert hit(rl, "analyze", "a", 1, 3600) is None and hit(rl, "analyze", "a", 1, 3600) is not None
    assert hit(rl, "analyze", "b", 1, 3600) is None                 # another visitor
    assert hit(rl, "evaluate", "a", 1, 3600) is None                # another endpoint group, same visitor


def test_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_KEYS", 5)
    clock = Clock()
    rl = RateLimiter(clock)
    for i in range(5):
        hit(rl, "analyze", f"ip{i}", 1, 60)
    clock.t += 61                                                  # all five have expired
    hit(rl, "analyze", "new", 1, 60)
    assert len(rl._hits) == 1


def test_client_ip_uses_the_last_forwarded_entry_not_a_forged_first_one():
    def req(headers, host="10.0.0.1"):
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))
    assert client_ip(req({"x-forwarded-for": "6.6.6.6, 203.0.113.7"})) == "203.0.113.7"
    assert client_ip(req({"x-forwarded-for": "203.0.113.7"})) == "203.0.113.7"
    assert client_ip(req({})) == "10.0.0.1"


def test_dependency_is_a_no_op_outside_hosted_mode(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_settings", lambda: SimpleNamespace(hosted=False, hosted_rate_analyze=1, access_code=None))
    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="x"), app=SimpleNamespace(state=SimpleNamespace()))
    dep = rate_limit("analyze")
    for _ in range(5):                                              # no limiter on app.state: it must not even be touched
        assert asyncio.run(dep(req)) is None


# ---------------------------------------------------------------- over HTTP, in hosted mode
def _client(monkeypatch, **env):
    monkeypatch.setenv("CHESSY_HOSTED", "1")
    monkeypatch.delenv("CHESSY_ACCESS_CODE", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_settings():
    yield
    get_settings.cache_clear()


def _analyze(c, ip="9.9.9.1", **headers):
    return c.post("/api/games/x/analyze", json={}, headers={"x-forwarded-for": ip, **headers})


def test_analyze_returns_429_with_a_clear_message_after_the_limit(monkeypatch):
    with _client(monkeypatch, HOSTED_RATE_ANALYZE="2") as c:
        assert [_analyze(c).status_code != 429 for _ in range(2)] == [True, True]
        r = _analyze(c)
    assert r.status_code == 429
    assert "Rate limit reached" in r.json()["detail"] and "2 game analyses per hour" in r.json()["detail"]
    assert int(r.headers["retry-after"]) > 0


def test_each_visitor_has_their_own_budget_and_a_forged_first_hop_does_not_help(monkeypatch):
    with _client(monkeypatch, HOSTED_RATE_ANALYZE="1") as c:
        assert _analyze(c, ip="5.5.5.5, 8.8.8.8").status_code != 429
        assert _analyze(c, ip="6.6.6.6, 8.8.8.8").status_code == 429    # same real address, different forged prefix
        assert _analyze(c, ip="8.8.4.4").status_code != 429             # a different visitor is untouched


def test_endpoints_are_limited_independently(monkeypatch):
    with _client(monkeypatch, HOSTED_RATE_ANALYZE="1", HOSTED_RATE_EVALUATE="1", HOSTED_RATE_COACH="1") as c:
        h = {"x-forwarded-for": "7.7.7.7"}
        assert c.post("/api/games/x/analyze", json={}, headers=h).status_code != 429
        assert c.post("/api/games/x/analyze", json={}, headers=h).status_code == 429
        assert c.post("/api/positions/evaluate", json={}, headers=h).status_code != 429     # own budget, untouched
        assert c.post("/api/positions/evaluate", json={}, headers=h).status_code == 429
        assert c.post("/api/coach/chat", json={}, headers=h).status_code != 429
        assert c.post("/api/coach/chat", json={}, headers=h).status_code == 429


def test_zero_disables_a_limit(monkeypatch):
    with _client(monkeypatch, HOSTED_RATE_ANALYZE="0") as c:
        assert all(_analyze(c).status_code != 429 for _ in range(15))


def test_the_budget_comes_back_when_the_window_passes(monkeypatch):
    with _client(monkeypatch, HOSTED_RATE_ANALYZE="1") as c:
        clock = Clock()
        c.app.state.limiter = RateLimiter(clock)
        assert _analyze(c).status_code != 429 and _analyze(c).status_code == 429
        clock.t += 3601
        assert _analyze(c).status_code != 429


def test_defaults_match_the_agreed_budgets(monkeypatch):
    with _client(monkeypatch):
        s = get_settings()
    assert (s.hosted_rate_analyze, s.hosted_rate_evaluate, s.hosted_rate_coach, s.hosted_rate_window) == (10, 120, 30, 3600)


def test_visitors_with_the_access_code_are_exempt(monkeypatch):
    with _client(monkeypatch, HOSTED_RATE_ANALYZE="1", CHESSY_ACCESS_CODE="s3cret") as c:
        with_code = [_analyze(c, **{"x-access-code": "s3cret"}).status_code for _ in range(4)]
        without = _analyze(c).status_code
    assert 429 not in with_code
    assert without == 401                                           # the access gate still applies to everyone else


@needs_engine
def test_a_real_evaluate_is_rate_limited_after_the_budget(monkeypatch):
    body = {"fen": "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1", "moves": [], "depth": 8, "multipv": 1}
    with _client(monkeypatch, HOSTED_RATE_EVALUATE="2") as c:
        codes = [c.post("/api/positions/evaluate", json=body, headers={"x-forwarded-for": "4.4.4.4"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------------------------------------------------------------- shared counts (Upstash Redis)
class FakeStore:
    """What UpstashStore does server side: a fixed-window counter per key, on a clock we control."""
    def __init__(self, clock):
        self.clock, self.data, self.calls, self.fail = clock, {}, [], False

    async def incr(self, key, window):
        self.calls.append(key)
        if self.fail:
            raise StoreError("ConnectError")
        n, expires = self.data.get(key, (0, 0))
        if expires <= self.clock():
            n, expires = 0, self.clock() + window
        self.data[key] = (n + 1, expires)
        return n + 1, int(expires - self.clock())


def test_two_instances_sharing_a_store_share_one_budget():
    store = FakeStore(Clock())
    a, b = SharedRateLimiter(store), SharedRateLimiter(store)          # two serverless instances
    assert [hit(a, "analyze", "1.1.1.1", 3, 3600) for _ in range(3)] == [None] * 3
    assert hit(b, "analyze", "1.1.1.1", 3, 3600) == 3600                # B knows about A's hits
    assert hit(b, "analyze", "2.2.2.2", 3, 3600) is None                # another visitor is untouched


def test_in_memory_limiters_do_not_share_which_is_the_gap_this_closes():
    a, b = RateLimiter(Clock()), RateLimiter(Clock())
    assert [hit(a, "analyze", "ip", 1, 3600), hit(b, "analyze", "ip", 1, 3600)] == [None, None]


def test_shared_window_expires_and_retry_after_is_the_time_left():
    clock = Clock()
    rl = SharedRateLimiter(FakeStore(clock))
    hit(rl, "analyze", "ip", 1, 3600)
    clock.t += 600
    assert hit(rl, "analyze", "ip", 1, 3600) == 3000
    clock.t += 3000
    assert hit(rl, "analyze", "ip", 1, 3600) is None


def test_groups_are_counted_separately_in_the_shared_store():
    rl = SharedRateLimiter(FakeStore(Clock()))
    assert hit(rl, "analyze", "a", 1, 60) is None and hit(rl, "analyze", "a", 1, 60) is not None
    assert hit(rl, "evaluate", "a", 1, 60) is None


def test_the_store_never_sees_a_raw_ip():
    store = FakeStore(Clock())
    hit(SharedRateLimiter(store), "coach", "203.0.113.7", 5, 60)
    assert store.calls and all("203.0.113.7" not in k and k.startswith("chessy:rl:coach:") for k in store.calls)


def test_a_store_outage_falls_back_to_memory_still_limits_and_warns_once(caplog):
    store = FakeStore(Clock())
    rl = SharedRateLimiter(store, RateLimiter(Clock()))
    store.fail = True
    with caplog.at_level(logging.INFO, logger="chessy.ratelimit"):
        assert hit(rl, "analyze", "ip", 1, 3600) is None                 # allowed, not a 500
        assert hit(rl, "analyze", "ip", 1, 3600) is not None             # and still limited, per instance
        assert sum("unavailable" in r.message for r in caplog.records) == 1
        store.fail = False
        assert hit(rl, "analyze", "ip", 1, 3600) is None                 # back on the shared count
        assert any("back" in r.message for r in caplog.records)


def _upstash(handler):
    return UpstashStore("https://eu1-x.upstash.io/", "tok3n", transport=httpx.MockTransport(handler))


def test_upstash_store_sends_one_authenticated_pipeline_and_reads_count_and_ttl():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=[{"result": 4}, {"result": 0}, {"result": 3500}])
    assert asyncio.run(_upstash(handler).incr("chessy:rl:analyze:abc", 3600)) == (4, 3500)
    (req,) = seen
    assert str(req.url) == "https://eu1-x.upstash.io/pipeline" and req.headers["authorization"] == "Bearer tok3n"
    assert json.loads(req.content) == [["INCR", "chessy:rl:analyze:abc"], ["EXPIRE", "chessy:rl:analyze:abc", 3600, "NX"],
                                       ["TTL", "chessy:rl:analyze:abc"]]


def test_upstash_store_without_a_ttl_reports_the_whole_window():
    store = _upstash(lambda r: httpx.Response(200, json=[{"result": 1}, {"result": 0}, {"result": -1}]))
    assert asyncio.run(store.incr("k", 60)) == (1, 60)


def _raise_timeout(r):
    raise httpx.ConnectTimeout("slow")


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, text="boom"),
    lambda r: httpx.Response(401, json={"error": "WRONGPASS tok3n"}),
    lambda r: httpx.Response(200, text="<html>"),
    lambda r: httpx.Response(200, json=[{"error": "ERR"}, {"result": 0}, {"result": 1}]),
    lambda r: httpx.Response(200, json={"result": 1}),
    _raise_timeout,
], ids=["500", "401", "not-json", "command-error", "wrong-shape", "timeout"])
def test_upstash_failures_become_store_errors_without_leaking_the_token(handler):
    with pytest.raises(StoreError) as e:
        asyncio.run(_upstash(handler).incr("k", 60))
    assert "tok3n" not in str(e.value)


def test_make_limiter_uses_upstash_only_when_both_settings_are_present():
    def pick(url, token):
        return make_limiter(SimpleNamespace(upstash_redis_rest_url=url, upstash_redis_rest_token=token))
    assert isinstance(pick("https://x", "t"), SharedRateLimiter)
    assert all(isinstance(pick(u, t), RateLimiter) for u, t in [(None, None), ("https://x", None), (None, "t"), ("", "")])


def test_the_app_shares_counts_through_the_configured_store(monkeypatch):
    with _client(monkeypatch, HOSTED_RATE_ANALYZE="1", UPSTASH_REDIS_REST_URL="https://x.upstash.io",
                 UPSTASH_REDIS_REST_TOKEN="t") as c:
        assert isinstance(c.app.state.limiter, SharedRateLimiter)
        store = FakeStore(Clock())
        c.app.state.limiter = SharedRateLimiter(store)
        assert _analyze(c).status_code != 429 and _analyze(c).status_code == 429
        c.app.state.limiter = SharedRateLimiter(store)                  # a "new instance" with empty memory
        assert _analyze(c).status_code == 429


def test_the_app_uses_memory_counts_when_no_store_is_configured(monkeypatch):
    monkeypatch.delenv("UPSTASH_REDIS_REST_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_REST_TOKEN", raising=False)
    with _client(monkeypatch) as c:
        assert isinstance(c.app.state.limiter, RateLimiter)
