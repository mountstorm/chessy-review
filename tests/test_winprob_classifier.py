import pytest

from app.engine.classifier import ClassifierInput as I, MoveClass as C, classify
from app.engine.winprob import accuracy, win_prob


def test_win_prob_shape():
    assert win_prob(0) == pytest.approx(0.5)
    assert win_prob(300) > win_prob(100) > 0.5 > win_prob(-100) > win_prob(-300)
    assert win_prob(1000) > 0.97
    assert win_prob(5) + win_prob(-5) == pytest.approx(1)


def test_accuracy_monotonic():
    assert accuracy(0) == pytest.approx(100, abs=0.1)
    assert accuracy(0.05) > accuracy(0.2) > accuracy(0.6)
    assert 0 <= accuracy(1.0) <= 100


@pytest.mark.parametrize("before,after,expected", [
    (0.50, 0.50, C.BEST),
    (0.50, 0.49, C.EXCELLENT),
    (0.50, 0.46, C.GOOD),
    (0.50, 0.42, C.INACCURACY),
    (0.50, 0.33, C.MISTAKE),
    (0.50, 0.10, C.BLUNDER),
    (0.50, 0.00, C.BLUNDER),
])
def test_loss_tiers(before, after, expected):
    assert classify(I(before, after)) == expected


def test_played_best_always_best_tier():
    assert classify(I(0.5, 0.45, played_is_best=True)) in (C.BEST, C.GREAT)


def test_great_requires_only_move_gap():
    assert classify(I(0.55, 0.55, True, second_best_win=0.30)) == C.GREAT
    assert classify(I(0.55, 0.55, True, second_best_win=0.52)) == C.BEST
    # already totally winning -> not "great"
    assert classify(I(0.99, 0.99, True, second_best_win=0.5)) == C.BEST


def test_brilliant_needs_sound_sacrifice():
    assert classify(I(0.6, 0.6, True, sacrifice=True)) == C.BRILLIANT
    assert classify(I(0.6, 0.3, False, sacrifice=True)) == C.BLUNDER      # unsound sac
    assert classify(I(0.97, 0.97, True, sacrifice=True)) == C.BEST         # already winning


def test_missed_win():
    # winning (0.85) but dropped to a merely-equal position
    assert classify(I(0.85, 0.55)) == C.MISSED_WIN
    # winning to lost is a blunder, not a "missed win"
    assert classify(I(0.85, 0.10)) == C.BLUNDER
    # forced mate overlooked, still fine afterwards
    assert classify(I(0.99, 0.80, best_is_mate=True, keeps_mate=False)) == C.MISSED_WIN
    assert classify(I(0.99, 0.80, best_is_mate=True, keeps_mate=True)) != C.MISSED_WIN
