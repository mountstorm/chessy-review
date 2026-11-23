"""Game cards show when a game was played, so both the hosted import entries and local GameSummary carry the PGN date."""
import pytest
from fastapi.testclient import TestClient

from app.api.games import _summary
from app.config import get_settings
from app.main import create_app
from app.models.db import Game
from app.services import pgn as pgnlib

DATED = ('[White "mountstorm"]\n[Black "rival"]\n[Result "1-0"]\n[Date "2026.11.20"]\n[UTCDate "2026.11.21"]\n'
         '[TimeControl "180+2"]\n\n1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0')
UNDATED = '[White "a"]\n[Black "b"]\n[Result "*"]\n\n1. e4 e5 *'


def _game(pgn_text):
    pg = pgnlib.parse_pgn(pgn_text)[0]
    m = pg.meta
    return Game(id="g1", source="pgn", source_id=pg.source_id, white=m["white"], black=m["black"], white_elo=m["white_elo"],
                black_elo=m["black_elo"], result=m["result"], eco=m["eco"], opening=m["opening"],
                time_control=m["time_control"], meta=m, pgn=pg.pgn, tree=pg.tree)


def test_local_summary_carries_the_utc_date_when_there_is_one():
    assert _summary(_game(DATED), analyzed=False).date == "2026.11.21"       # UTCDate wins over the local Date


def test_local_summary_without_a_date_is_none_not_an_error():
    assert _summary(_game(UNDATED), analyzed=False).date is None


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("CHESSY_HOSTED", "1")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def test_hosted_import_entries_carry_date_and_time_control_at_the_top_level(hosted):
    g = hosted.post("/api/games/import", json={"pgn": DATED}).json()["games"][0]
    assert g["date"] == "2026.11.21" and g["time_control"]["base"] == 180 and g["time_control"]["increment"] == 2
    assert hosted.post("/api/games/import", json={"pgn": UNDATED}).json()["games"][0]["date"] is None


@pytest.mark.parametrize("headers,expected", [
    ({"UTCDate": "2026.11.21", "Date": "2026.11.20"}, "2026.11.21"),          # the UTC date wins
    ({"Date": "2026.11.20"}, "2026.11.20"),
    ({"Date": "2026.??.??"}, "2026.??.??"),                                  # partial dates are kept for the UI to trim
    ({"Date": "????.??.??"}, None),                                          # python-chess's placeholder for "unknown"
    ({"UTCDate": "????.??.??", "Date": "2026.11.20"}, "2026.11.20"),         # an unknown UTC date doesn't hide a real PGN date
    ({}, None),
])
def test_game_date_ignores_the_unknown_placeholder(headers, expected):
    assert pgnlib._game_date(headers) == expected
