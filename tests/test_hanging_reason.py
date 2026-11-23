"""The 'hanging' markers say why a piece was flagged, so the UI can explain them instead of just drawing a red circle."""
import chess

from app.engine import motifs as mt
from app.engine.visuals import hanging_squares, position_visuals

UNDEFENDED = "4k3/8/8/4p3/3B4/8/8/4K3 w - - 0 1"        # Bd4 hangs to the e5 pawn and nothing guards it
CHEAPER = "4k3/8/8/4p3/3R4/8/8/3QK3 w - - 0 1"           # Rd4 is guarded by the queen but a pawn attacks it


def reasons(fen):
    return {chess.square_name(sq): why for sq, why in mt.hanging_reasons(chess.Board(fen))}


def test_unguarded_piece_is_flagged_undefended():
    assert reasons(UNDEFENDED)["d4"] == "undefended"


def test_guarded_piece_attacked_by_a_cheaper_one_says_so():
    assert reasons(CHEAPER)["d4"] == "by a cheaper piece"


def test_a_guarded_piece_attacked_by_an_equal_one_is_not_flagged():
    assert reasons("4k3/4r3/8/8/8/8/4R3/4K3 w - - 0 1") == {}


def test_visual_markers_carry_the_reason():
    d4 = next(h for h in hanging_squares(chess.Board(CHEAPER)) if h["square"] == "d4")
    assert d4 == {"square": "d4", "color": "white", "piece": "R", "reason": "by a cheaper piece"}
    assert all(h["reason"] in ("undefended", "by a cheaper piece") for h in position_visuals(chess.Board(UNDEFENDED))["hanging"])


def test_motif_text_is_unchanged_by_the_refactor():
    texts = {m.description for m in mt.hanging(chess.Board(CHEAPER))}
    assert "white rook on d4 can be captured (by a cheaper piece)" in texts
