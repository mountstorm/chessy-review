import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import importers
from conftest import needs_engine, parse_sse

PGN = '''[Event "Casual"]
[Site "https://lichess.org/abcd1234"]
[White "Alice"]
[Black "Bob"]
[WhiteElo "1100"]
[BlackElo "1150"]
[Result "1-0"]
[ECO "C50"]
[Opening "Italian Game"]
[TimeControl "180+0"]

1. e4 {[%clk 0:03:00]} e5 {[%clk 0:03:00]} 2. Nf3 {[%clk 0:02:50]} Nc6 {[%clk 0:02:55]} 3. Bc4 {[%clk 0:02:40]} Nf6 {[%clk 0:00:20]}
4. Ng5 {[%clk 0:02:30]} d5 {[%clk 0:00:15]} 5. exd5 {[%clk 0:02:20]} Nxd5 {[%clk 0:00:10]} 6. Nxf7 {[%clk 0:02:10]} Kxf7 {[%clk 0:00:08]}
7. Qf3+ {[%clk 0:02:00]} Ke6 {[%clk 0:00:05]} 8. Nc3 Nb4 9. Qe4 1-0
'''


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def test_health(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and "Stockfish" in h["engine"] and h["coach"] == "offline"


def test_import_validation(client):
    assert client.post("/api/games/import", json={}).status_code == 422
    assert client.post("/api/games/import", json={"pgn": "x", "lichess_username": "y"}).status_code == 422
    assert client.post("/api/games/import", json={"pgn": "1. e4 e5 2. Nf6"}).status_code == 422
    assert client.get("/api/games/nope").status_code == 404


@needs_engine
def test_full_flow(client):
    # import (+dedupe)
    r = client.post("/api/games/import", json={"pgn": PGN})
    assert r.status_code == 200
    g = r.json()["games"][0]
    assert (g["white"], g["black"], g["plies"], g["created"], g["analyzed"]) == ("Alice", "Bob", 17, True, False)
    assert client.post("/api/games/import", json={"pgn": PGN}).json()["games"][0]["created"] is False
    gid = g["id"]
    full = client.get(f"/api/games/{gid}").json()
    assert full["tree"]["children"][0]["san"] == "e4" and full["meta"]["opening"] == "Italian Game"

    # insights before analysis
    assert client.get("/api/players/alice/insights").json()["games_analyzed"] == 0
    assert client.get("/api/players/nobody/insights").status_code == 404

    # streaming analysis
    with client.stream("POST", f"/api/games/{gid}/analyze", json={"depth": 10, "multipv": 3}) as s:
        assert s.headers["content-type"].startswith("text/event-stream")
        body = "".join(s.iter_text())
    ev = parse_sse(body)
    kinds = [k for k, _ in ev]
    assert kinds[0] == "start" and kinds[-1] == "done" and kinds.count("move") == 17 and "summary" in kinds
    assert ev[0][1]["replay"] is False
    moves = [d["move"] for k, d in ev if k == "move"]
    assert [m["ply"] for m in moves] == list(range(1, 18))
    assert ev[1][1]["progress"] == {"done": 1, "total": 17}
    summary = [d for k, d in ev if k == "summary"][0]["summary"]
    assert summary["sides"]["black"]["moves"] == 8 and len(summary["eval"]) == 18

    # second run is replayed from the DB, instantly
    with client.stream("POST", f"/api/games/{gid}/analyze", json={"depth": 10, "multipv": 3}) as s:
        ev2 = parse_sse("".join(s.iter_text()))
    assert ev2[0][1]["replay"] is True and ev2[-1][1]["cached"] is True and len([k for k, _ in ev2 if k == "move"]) == 17
    rev = client.get(f"/api/games/{gid}/review").json()
    assert rev["depth"] == 10 and len(rev["moves"]) == 17

    # what-if branch explorer, cached on repeat
    body = {"fen": moves[3]["fen_after"], "moves": ["Bc4", "Nf6"], "depth": 10, "multipv": 4}
    a = client.post("/api/positions/evaluate", json=body).json()
    assert len(a["lines"]) == 4 and a["played"] == ["Bc4", "Nf6"] and a["side_to_move"] == "white"
    assert a["lines"][0]["pv_san"] and 0 <= a["eval"]["win_white"] <= 1 and a["visuals"]["heatmap"]
    assert client.post("/api/positions/evaluate", json=body).json()["cached"] is True
    bad = client.post("/api/positions/evaluate", json={"moves": ["Ke5"]})
    assert bad.status_code == 422 and "Nf3" in bad.json()["detail"]
    assert client.post("/api/positions/evaluate", json={"fen": "garbage"}).status_code == 422

    # coach (offline provider here: grounded by construction)
    with client.stream("POST", "/api/coach/chat", json={
            "game_id": gid, "current_fen": moves[5]["fen_before"], "move_history": [m["san"] for m in moves[:5]],
            "user_message": "what should I do?", "mode": "explain"}) as s:
        cev = parse_sse("".join(s.iter_text()))
    ck = [k for k, _ in cev]
    assert ck[0] == "grounding" and ck[-1] == "done" and "text" in ck and "ui" in ck
    assert client.post("/api/coach/chat", json={"current_fen": "bad", "mode": "explain"}).status_code == 422
    assert client.post("/api/coach/chat", json={"current_fen": moves[0]["fen_before"], "mode": "oops"}).status_code == 422

    # cross-game insights
    ins = client.get("/api/players/ALICE/insights").json()
    assert ins["games_analyzed"] == 1 and ins["moves_analyzed"] == 9
    assert set(ins["accuracy_by_phase"]) <= {"opening", "middlegame", "endgame"} and "opening" in ins["accuracy_by_phase"]
    bob = client.get("/api/players/bob/insights").json()
    assert bob["blunder_rate"]["low_clock_moves"] >= 5          # Bob's clock sat under 30s
    assert isinstance(bob["weakest_openings"], list) and bob["weakest_openings"][0]["opening"] == "Italian Game"
    assert "verdict" in bob["blunder_rate"]


# ---------------------------------------------------------------- importers against a mocked network
def _mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_chesscom_importer():
    def handler(req: httpx.Request):
        assert "chessy" in req.headers["user-agent"]
        if req.url.path.endswith("/games/archives"):
            return httpx.Response(200, json={"archives": ["https://api.chess.com/pub/player/magnus/games/2024/01",
                                                          "https://api.chess.com/pub/player/magnus/games/2024/02"]})
        if req.url.path.endswith("2024/02"):
            return httpx.Response(200, json={"games": [{"pgn": "[White \"a\"]\n[Black \"b\"]\n\n1. e4 e5 *"},
                                                       {"pgn": "[White \"c\"]\n[Black \"d\"]\n\n1. d4 d5 *"}]})
        return httpx.Response(404)
    async with _mock(handler) as c:
        text = await importers.fetch_chesscom("Magnus", 5, c)
    assert text.index('"c"') < text.index('"a"')          # newest game first
    async with _mock(lambda r: httpx.Response(404)) as c:
        with pytest.raises(importers.ImportFailed) as e:
            await importers.fetch_chesscom("ghost", 3, c)
        assert e.value.status == 404


async def test_lichess_importer_and_username_validation():
    def handler(req: httpx.Request):
        assert req.headers["accept"] == "application/x-chess-pgn" and req.url.params["clocks"] == "true"
        return httpx.Response(200, text=PGN)
    async with _mock(handler) as c:
        assert "Alice" in await importers.fetch_lichess("someone", 3, c)
    with pytest.raises(importers.ImportFailed) as e:
        await importers.fetch_lichess("bad name!!", 3)
    assert e.value.status == 422


def test_the_home_preview_images_are_served_as_webp(client):
    for name in ("preview-dark.webp", "preview-light.webp"):
        r = client.get(f"/assets/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/webp" and r.content[8:12] == b"WEBP"
