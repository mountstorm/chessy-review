import chess
import pytest

from app.engine import motifs as mt
from app.engine.visuals import king_safety, mobility, control_heatmap


def types(fen, side=None):
    return {(m.type) for m in mt.detect(chess.Board(fen)) if side is None or m.side == side}


def test_fork():
    ms = [m for m in mt.detect(chess.Board("r3k3/2N5/8/8/8/8/8/4K3 b - - 0 1")) if m.type == "fork"]
    assert ms and ms[0].side == "white" and ms[0].squares[0] == "c7" and set(ms[0].squares[1:]) == {"a8", "e8"}


def test_absolute_pin():
    ms = [m for m in mt.detect(chess.Board("4k3/4r3/8/8/8/8/4R3/4K3 w - - 0 1")) if m.type == "pin"]
    assert any(m.side == "black" and m.squares == ("e7", "e2", "e1") for m in ms)


def test_skewer():
    assert "skewer" in types("r3k3/8/8/q7/8/8/8/R3K3 w - - 0 1", "white")


def test_hanging_piece():
    ms = [m for m in mt.detect(chess.Board("4k3/8/8/4p3/3B4/8/8/4K3 w - - 0 1")) if m.type == "hanging"]
    by_side = {m.side: m.squares for m in ms}
    assert by_side == {"black": ("d4",), "white": ("e5",)}     # bishop hangs to the pawn; the loose pawn hangs to the bishop


def test_defended_equal_piece_not_hanging():
    assert "hanging" not in types("4k3/4r3/8/8/8/8/4R3/4K3 w - - 0 1")


def test_discovered_attack():
    assert "discovered_attack" in types("4k3/8/8/4q3/4B3/8/8/4R1K1 w - - 0 1", "white")


def test_start_position_is_quiet():
    assert mt.detect(chess.Board()) == []


def test_move_creates_fork_and_mate():
    b = chess.Board("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1")           # Nb5-c7+ forks king and rook
    created = mt.move_creates(b, chess.Move.from_uci("b5c7"))
    assert any(m.type == "fork" and m.squares[0] == "c7" for m in created)
    mate = chess.Board("6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1")
    assert any(m.type == "mate" for m in mt.move_creates(mate, chess.Move.from_uci("a1a8")))


def test_move_creates_free_capture():
    b = chess.Board("4k3/8/8/4p3/3B4/8/8/4K3 b - - 0 1")
    created = mt.move_creates(b, chess.Move.from_uci("e5d4"))
    assert any(m.type == "hanging" for m in created)


def test_visuals_symmetry_and_ranges():
    b = chess.Board()
    assert king_safety(b, chess.WHITE) == king_safety(b, chess.BLACK)
    assert 0 <= king_safety(b, chess.WHITE) <= 100
    assert mobility(b, chess.WHITE) == mobility(b, chess.BLACK) == 20
    h = control_heatmap(b)
    assert len(h) == 64 and h[chess.E3] > 0 > h[chess.E6]


def test_exposed_king_scores_lower():
    safe = chess.Board("6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1")
    exposed = chess.Board("6k1/5ppp/8/8/8/8/8/3q2K1 w - - 0 1")
    assert king_safety(exposed, chess.WHITE) < king_safety(safe, chess.WHITE)
