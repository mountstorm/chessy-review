"""The precomputed example review (app/data/example.json) must keep matching what the analysis really produces.
If test_example_matches_live_response_format fails, regenerate it: .venv/bin/python tools/build_example.py"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.engine.classifier import MoveClass
from app.engine.review import review_game
from app.main import create_app
from app.services import pgn as pgnlib
from app.services.example import EXAMPLE_PGN, load_example
from conftest import needs_engine


LABELS = {c.value for c in MoveClass}
OPTIONAL_KEYS = {"minor"}                        # only present on moments that are not real turning points


def compatible(saved, live, path="$"):
    """Same structure: dicts share exactly the same keys, scalars the same kind. None and empty lists can stand for
    'nothing here this time' (e.g. no threat on a move), so they match anything."""
    if saved is None or live is None or saved == [] or live == []:
        return []
    if isinstance(saved, dict) and isinstance(live, dict):
        if path.endswith(".counts"):             # {label: n} for the labels that occurred, so the keys legitimately vary
            return [f"{path}: unknown labels {sorted(set(saved) | set(live) - LABELS)}"] if (set(saved) | set(live)) - LABELS else []
        differ = (saved.keys() ^ live.keys()) - OPTIONAL_KEYS
        out = [f"{path}: keys differ: {sorted(differ)}"] if differ else []
        for k in saved.keys() & live.keys():
            out += compatible(saved[k], live[k], f"{path}.{k}")
        return out
    if isinstance(saved, list) and isinstance(live, list):
        return compatible(saved[0], live[0], f"{path}[]")
    num = (int, float)
    if isinstance(saved, bool) or isinstance(live, bool):
        return [] if type(saved) is type(live) else [f"{path}: {type(saved).__name__} vs {type(live).__name__}"]
    if isinstance(saved, num) and isinstance(live, num):
        return []
    return [] if type(saved) is type(live) else [f"{path}: {type(saved).__name__} vs {type(live).__name__}"]


def test_example_moves_are_the_example_games_moves():
    ex = load_example()
    nodes = pgnlib.mainline_nodes(pgnlib.parse_pgn(EXAMPLE_PGN)[0].tree)
    assert [m["san"] for m in ex["moves"]] == [n["san"] for n in nodes]
    assert [m["ply"] for m in ex["moves"]] == list(range(1, len(nodes) + 1))
    assert ex["start"]["total"] == len(nodes) and len(ex["summary"]["eval"]) == len(nodes) + 1
    assert ex["depth"] == 18 and ex["summary"]["moments"]


@needs_engine
async def test_example_matches_live_response_format(pool):
    ex = load_example()
    g = pgnlib.parse_pgn(EXAMPLE_PGN)[0]
    events = [e async for e in review_game(pool, g.tree["fen"], pgnlib.mainline_nodes(g.tree), 8, ex["multipv"])]
    live_moves = [e["move"] for e in events if e["type"] == "move"]
    live_start, live_summary = events[0], events[-1]["summary"]
    problems = compatible(ex["start"], {k: live_start[k] for k in ("fen", "eval", "total")}, "start")
    for saved, live in zip(ex["moves"], live_moves):
        problems += compatible(saved, live, f"moves[{saved['ply']}]")
    from app.coach.offline import with_moments
    problems += compatible(ex["summary"], with_moments(live_summary, live_moves), "summary")
    assert len(live_moves) == len(ex["moves"])
    assert not problems, "example.json is stale, run tools/build_example.py:\n" + "\n".join(problems[:10])


def test_compatible_catches_a_renamed_key():
    assert compatible({"a": 1, "b": {"c": "x"}}, {"a": 2, "b": {"c": "y"}}) == []
    assert compatible({"a": 1}, {"b": 1}) and compatible({"a": {"c": 1}}, {"a": {"d": 1}})
    assert compatible({"a": 1}, {"a": "1"})


def test_example_endpoint_stores_the_review_and_is_idempotent():
    with TestClient(create_app()) as c:
        for _ in range(2):
            r = c.post("/api/games/example").json()
            g = r["games"][0]
            assert g["analyzed"] and g["plies"] == 23 and r["example"]["moves"][10]["label"] == "brilliant"
        assert len([x for x in c.get("/api/games").json()["games"] if x["id"] == g["id"]]) == 1
        rev = c.get(f"/api/games/{g['id']}/review").json()
        assert rev["depth"] == 18 and [m["label"] for m in rev["moves"]] == [m["label"] for m in r["example"]["moves"]]


def test_example_endpoint_in_hosted_mode(monkeypatch):
    monkeypatch.setenv("CHESSY_HOSTED", "1")
    monkeypatch.delenv("CHESSY_ACCESS_CODE", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            r = c.post("/api/games/example").json()
        assert r["hosted"] and r["games"][0]["id"].startswith("h") and r["games"][0]["tree"]["children"]
        assert r["example"]["summary"]["moments"] and len(r["example"]["moves"]) == 23
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
