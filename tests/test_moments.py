"""The 'moments that decided this game' summary: one third-person sentence per turning point, chosen from the review's key moments."""
import pytest
from fastapi.testclient import TestClient

from app.coach.offline import MOMENT_LIMIT, MOMENT_MIN_LOSS, build_moments, moment_sentence, with_moments
from app.config import get_settings
from app.main import create_app
from conftest import needs_engine, parse_sse


def mv(ply, san="Bd6", mover="black", label="blunder", loss=0.43, best="Kc8", missed=None):
    return {"ply": ply, "san": san, "mover": mover, "label": label, "loss": loss, "missed_motifs": missed or [],
            "best": {"san": best, "uci": "c7c8", "pv_san": [best]} if best else None}


# ---------------------------------------------------------------- the sentence
def test_sentence_names_the_side_the_cost_and_the_better_move():
    assert moment_sentence(mv(26)) == "Black's Bd6 cost 43% win chance; Kc8 was the better move."


def test_sentence_says_what_the_better_move_would_have_set_up_mate_first_and_at_most_two_ideas():
    s = moment_sentence(mv(50, "Bxf5", missed=["skewer", "hanging", "mate", "fork"]))
    assert s == "Black's Bxf5 cost 43% win chance; Kc8 was the better move, setting up a forced mate and a fork."
    assert "skewer" not in s and "loose piece" not in s                       # capped at two, mate ranked first


def test_which_ideas_are_mentioned_does_not_depend_on_the_order_the_review_listed_them():
    a = moment_sentence(mv(1, missed=["discovered_attack", "hanging", "skewer"]))         # the review lists them alphabetically
    b = moment_sentence(mv(1, missed=["skewer", "hanging", "discovered_attack"]))
    assert a == b == "Black's Bd6 cost 43% win chance; Kc8 was the better move, setting up a skewer and a discovered attack."


def test_sentence_for_a_missed_win_says_a_win_slipped_away():
    s = moment_sentence(mv(39, "exd5", "white", "missed_win", 0.52, "Qxh6", ["pin"]))
    assert s == "White's exd5 let a win slip away, costing 52% win chance; Qxh6 was the better move, setting up a pin."


def test_sentence_is_one_sentence_and_never_says_you():
    for m in (mv(1), mv(2, missed=["fork"]), mv(3, label="missed_win"), mv(4, best=None)):
        s = moment_sentence(m)
        assert s.endswith(".") and s.count(".") == 1 and "you" not in s.lower()


def test_sentence_without_a_known_better_move_stops_after_the_cost():
    assert moment_sentence(mv(9, best=None, loss=0.2)) == "Black's Bd6 cost 20% win chance."


def test_unknown_motif_types_are_ignored_not_shown():
    assert "mystery" not in moment_sentence(mv(1, missed=["mystery"])) and moment_sentence(mv(1, missed=["mystery"])).endswith("better move.")


# ---------------------------------------------------------------- which moments
def test_only_real_turning_points_are_kept_in_game_order():
    moves = [mv(26, loss=0.43), mv(4, loss=0.13), mv(5, loss=0.10), mv(49, loss=0.30)]
    got = build_moments(moves, [26, 4, 5, 49])                                # key_moments arrive biggest-first
    assert [m["ply"] for m in got] == [26, 49]                                # the 13% and 10% slips are not turning points, and order is by ply


def test_at_most_three_moments_taking_the_biggest_ones_in_key_moment_order():
    moves = [mv(p, loss=l) for p, l in [(10, .9), (20, .8), (30, .7), (40, .6), (5, .5)]]
    got = build_moments(moves, [10, 20, 30, 40, 5])                           # the review hands over the largest losses first
    assert MOMENT_LIMIT == 3 and [m["ply"] for m in got] == [10, 20, 30]


def test_a_clean_game_has_no_moments_and_the_threshold_is_inclusive():
    assert build_moments([mv(3, loss=0.14)], [3]) == []
    assert [m["ply"] for m in build_moments([mv(3, loss=MOMENT_MIN_LOSS)], [3])] == [3]
    assert build_moments([], []) == []


def test_key_moments_that_are_not_in_the_move_list_are_skipped():
    assert build_moments([mv(7)], [99, 7]) == [{"ply": 7, "sentence": moment_sentence(mv(7))}]


# ---------------------------------------------------------------- the summary
def test_with_moments_fills_in_reviews_saved_before_this_existed_and_leaves_new_ones_alone():
    old = {"key_moments": [26], "sides": {}}
    filled = with_moments(old, [mv(26)])
    assert [m["ply"] for m in filled["moments"]] == [26] and "moments" not in old          # the stored summary isn't mutated
    already = {"key_moments": [26], "moments": [{"ply": 1, "sentence": "kept"}]}
    assert with_moments(already, [mv(26)]) is already
    assert with_moments({}, [mv(26)])["moments"] == []                                      # no key_moments at all


# ---------------------------------------------------------------- end to end (hosted analysis streams the summary)
FOOLS_MATE = "[White \"a\"]\n[Black \"b\"]\n\n1. f3 e5 2. g4 Qh4# 0-1"


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("CHESSY_HOSTED", "1")
    monkeypatch.setenv("HOSTED_RATE_ANALYZE", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


@needs_engine
def test_a_real_analysis_reports_the_losing_move_as_a_turning_point(hosted):
    r = hosted.post("/api/games/x/analyze", json={"pgn": FOOLS_MATE, "depth": 8, "multipv": 2})
    summary = next(e for e in parse_sse(r.text) if e[0] == "summary")[1]["summary"]
    moments = {m["ply"]: m["sentence"] for m in summary["moments"]}
    assert 3 in moments and moments[3].startswith("White's g4 ")             # 2. g4?? walks into mate in one
    assert "you" not in moments[3].lower() and 3 in summary["key_moments"]
