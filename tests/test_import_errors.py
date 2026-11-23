"""What a player sees when a Chess.com / Lichess import goes wrong: a plain message and a meaningful status."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services import importers

ARCHIVES = "https://api.chess.com/pub/player/magnus/games/archives"
MONTH_OLD = "https://api.chess.com/pub/player/magnus/games/2024/01"
MONTH_NEW = "https://api.chess.com/pub/player/magnus/games/2024/02"
GAME = {"pgn": "[White \"a\"]\n[Black \"b\"]\n\n1. e4 e5 *"}


def _mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _fail(coro):
    with pytest.raises(importers.ImportFailed) as e:
        await coro
    return e.value


# ---------------------------------------------------------------- Chess.com
async def test_chesscom_unknown_user_is_a_404_naming_the_user():
    async with _mock(lambda r: httpx.Response(404)) as c:
        err = await _fail(importers.fetch_chesscom("ghost", 3, c))
    assert err.status == 404 and "'ghost' not found" in str(err)


async def test_chesscom_user_with_no_games_says_so():
    async with _mock(lambda r: httpx.Response(200, json={"archives": []})) as c:
        err = await _fail(importers.fetch_chesscom("newbie", 3, c))
    assert err.status == 404 and "No recent games found for 'newbie'" in str(err)


async def test_chesscom_rate_limit_is_a_429_with_a_plain_message():
    async with _mock(lambda r: httpx.Response(429)) as c:
        err = await _fail(importers.fetch_chesscom("magnus", 3, c))
    assert err.status == 429 and "rate-limiting" in str(err) and "Wait a minute" in str(err)
    assert "Client error" not in str(err) and "api.chess.com" not in str(err)                    # no raw client error text


async def test_chesscom_rate_limited_on_the_first_month_is_still_a_429():
    def handler(req):
        return httpx.Response(200, json={"archives": [MONTH_OLD]}) if req.url.path.endswith("/archives") else httpx.Response(429)
    async with _mock(handler) as c:
        err = await _fail(importers.fetch_chesscom("magnus", 3, c))
    assert err.status == 429


async def test_chesscom_rate_limit_on_an_older_month_keeps_the_games_already_fetched():
    def handler(req):
        if req.url.path.endswith("/archives"):
            return httpx.Response(200, json={"archives": [MONTH_OLD, MONTH_NEW]})
        return httpx.Response(200, json={"games": [GAME]}) if req.url.path.endswith("2024/02") else httpx.Response(429)
    async with _mock(handler) as c:
        assert "e4" in await importers.fetch_chesscom("magnus", 5, c)


async def test_chesscom_server_error_timeout_and_network_failure_each_get_their_own_message():
    async with _mock(lambda r: httpx.Response(503)) as c:
        e503 = await _fail(importers.fetch_chesscom("magnus", 3, c))

    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    async with _mock(slow) as c:
        etime = await _fail(importers.fetch_chesscom("magnus", 3, c))

    def down(req):
        raise httpx.ConnectError("no route", request=req)

    async with _mock(down) as c:
        edown = await _fail(importers.fetch_chesscom("magnus", 3, c))
    assert (e503.status, etime.status, edown.status) == (502, 504, 502)
    assert "having trouble" in str(e503) and "(error 503)" in str(e503)
    assert "took too long" in str(etime) and "Couldn't reach Chess.com" in str(edown)


# ---------------------------------------------------------------- Lichess
async def test_lichess_unknown_user_no_games_and_rate_limit():
    async with _mock(lambda r: httpx.Response(404)) as c:
        nf = await _fail(importers.fetch_lichess("ghost", 3, c))
    async with _mock(lambda r: httpx.Response(200, text="  \n")) as c:
        empty = await _fail(importers.fetch_lichess("newbie", 3, c))
    async with _mock(lambda r: httpx.Response(429)) as c:
        limited = await _fail(importers.fetch_lichess("someone", 3, c))
    assert nf.status == 404 and "'ghost' not found" in str(nf)
    assert empty.status == 404 and "No recent games found for 'newbie'" in str(empty)
    assert limited.status == 429 and "Lichess is rate-limiting" in str(limited)


# ---------------------------------------------------------------- through the API
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHESSY_HOSTED", "1")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


@pytest.mark.parametrize("site,field,fn", [("Chess.com", "chesscom_username", "fetch_chesscom"),
                                           ("Lichess", "lichess_username", "fetch_lichess")])
def test_api_returns_the_importer_status_and_message(client, monkeypatch, site, field, fn):
    async def limited(*a, **k):
        raise importers.ImportFailed(f"{site} is rate-limiting requests right now. Wait a minute and try again.", 429)
    monkeypatch.setattr(importers, fn, limited)
    r = client.post("/api/games/import", json={field: "someone"})
    assert r.status_code == 429 and r.json()["detail"].startswith(f"{site} is rate-limiting")
