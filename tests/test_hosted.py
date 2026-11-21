"""Hosted (serverless) mode: stateless API, per-request engines, caps, access code."""
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from conftest import needs_engine, parse_sse

SCHOLAR = "[White \"Alice\"]\n[Black \"Bob\"]\n[Opening \"Italian Game\"]\n\n1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0"


def _client(monkeypatch, **env):
    monkeypatch.setenv("CHESSY_HOSTED", "1")
    monkeypatch.delenv("CHESSY_ACCESS_CODE", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def hosted(monkeypatch):
    with _client(monkeypatch, HOSTED_MAX_DEPTH="9") as c:
        yield c
    monkeypatch.undo()
    get_settings.cache_clear()


def test_health_reports_hosted_mode(hosted):
    h = hosted.get("/api/health").json()
    assert h["hosted"] is True and h["coach"] == "offline" and h["needs_code"] is False and h["max_depth"] == 9


def test_import_is_stateless_and_db_routes_are_501(hosted):
    r = hosted.post("/api/games/import", json={"pgn": SCHOLAR}).json()
    g = r["games"][0]
    assert r["hosted"] and g["id"].startswith("h") and g["plies"] == 7 and g["tree"]["children"][0]["san"] == "e4" and g["pgn"]
    assert hosted.get("/api/games").status_code == 501
    assert hosted.get(f"/api/games/{g['id']}").status_code == 501
    assert hosted.get("/api/players/alice/insights").status_code == 501
    # same game imported twice gets the same id (browser can dedupe)
    assert hosted.post("/api/games/import", json={"pgn": SCHOLAR}).json()["games"][0]["id"] == g["id"]


@needs_engine
def test_analyze_from_posted_pgn_streams_and_caps_depth(hosted):
    with hosted.stream("POST", "/api/games/h1/analyze", json={"pgn": SCHOLAR, "depth": 30, "multipv": 3}) as s:
        ev = parse_sse("".join(s.iter_text()))
    kinds = [k for k, _ in ev]
    assert kinds[0] == "start" and kinds[-1] == "done" and kinds.count("move") == 7 and "summary" in kinds
    assert ev[0][1]["depth"] == 9                                            # asked for 30, capped by the host
    moves = [d["move"] for k, d in ev if k == "move"]
    assert moves[5]["san"] == "Nf6" and moves[5]["label"] == "blunder"


@needs_engine
def test_per_request_engines_are_cleaned_up(hosted):
    count = lambda: subprocess.run(["pgrep", "-fc", "stockfish"], capture_output=True, text=True).stdout.strip()
    before = count()
    with hosted.stream("POST", "/api/games/h1/analyze", json={"pgn": SCHOLAR, "depth": 8}) as s:
        "".join(s.iter_text())
    hosted.post("/api/positions/evaluate", json={"moves": ["e4"], "depth": 8})
    assert count() == before


def test_analyze_validation(hosted):
    assert hosted.post("/api/games/h1/analyze", json={"depth": 8}).status_code == 422              # pgn required
    assert hosted.post("/api/games/h1/analyze", json={"pgn": "1. e4 e5 2. Nf6"}).status_code == 422


def test_ply_cap(monkeypatch):
    with _client(monkeypatch, HOSTED_MAX_PLIES="4") as c:
        r = c.post("/api/games/h1/analyze", json={"pgn": SCHOLAR, "depth": 8})
        assert r.status_code == 422 and "up to 4 plies" in r.json()["detail"]
    monkeypatch.undo()
    get_settings.cache_clear()


@needs_engine
def test_explorer_and_coach_work_statelessly(hosted):
    a = hosted.post("/api/positions/evaluate", json={"moves": ["e4", "e5"], "depth": 8, "multipv": 3}).json()
    assert len(a["lines"]) == 3 and a["side_to_move"] == "white"
    fen = "6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1"
    body = {"current_fen": fen, "mode": "explain", "context": {"white": "Alice", "black": "Bob",
            "last_move": {"san": "Kd2", "label": "missed_win", "comment": "You missed a win here."}}}
    with hosted.stream("POST", "/api/coach/chat", json=body) as s:
        ev = parse_sse("".join(s.iter_text()))
    text = "".join(d["delta"] for k, d in ev if k == "text")
    assert ev[0][0] == "grounding" and ev[0][1]["provider"] == "offline" and "Ra8#" in text and "Kd2" in text


def test_masters_request_validation(hosted):
    assert hosted.post("/api/masters", json={"sans": []}).status_code == 422
    assert hosted.post("/api/masters", json={"sans": ["x" * 40]}).status_code == 422


@needs_engine
def test_access_code_gate(monkeypatch):
    with _client(monkeypatch, CHESSY_ACCESS_CODE="friends-only", HOSTED_MAX_DEPTH="8") as c:
        assert c.get("/api/health").json()["needs_code"] is True                    # health stays open so the UI can ask
        assert c.get("/").status_code == 200                                          # the page itself is open
        body = {"moves": ["e4"], "depth": 8}
        assert c.post("/api/positions/evaluate", json=body).status_code == 401
        assert c.post("/api/positions/evaluate", json=body, headers={"x-access-code": "nope"}).status_code == 401
        assert c.post("/api/positions/evaluate", json=body, headers={"x-access-code": "friends-only"}).status_code == 200
    monkeypatch.undo()
    get_settings.cache_clear()


def test_vercel_env_switches_on_hosted_mode(monkeypatch):
    monkeypatch.delenv("CHESSY_HOSTED", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.hosted and s.resolved_provider() == "offline"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
