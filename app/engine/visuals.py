"""Board-visual data for the frontend: king safety, mobility, control heatmap, hanging squares, threats."""
from __future__ import annotations

from typing import Dict, List, Optional

import chess

from .motifs import VAL, hanging


def king_safety(board: chess.Board, color: bool) -> int:
    """0-100 heuristic: pawn shield, attackers around the king, open files, uncastled king in the centre."""
    ksq = board.king(color)
    if ksq is None:
        return 0
    score = 100
    kf, kr = chess.square_file(ksq), chess.square_rank(ksq)
    fwd = 1 if color == chess.WHITE else -1
    shield = 0
    for df in (-1, 0, 1):
        f = kf + df
        if not 0 <= f <= 7:
            continue
        for dr in (1, 2):
            r = kr + fwd * dr
            if 0 <= r <= 7:
                p = board.piece_at(chess.square(f, r))
                if p and p.piece_type == chess.PAWN and p.color == color:
                    shield += 1
                    break
    score -= (3 - shield) * 12
    zone = chess.SquareSet(chess.BB_KING_ATTACKS[ksq]) | chess.SquareSet.from_square(ksq)
    attackers = set()
    for z in zone:
        for a in board.attackers(not color, z):
            attackers.add(a)
    score -= min(36, sum(6 + VAL[board.piece_at(a).piece_type] // 3 for a in attackers))
    for df in (-1, 0, 1):
        f = kf + df
        if 0 <= f <= 7:
            file_has_own_pawn = any(board.piece_at(chess.square(f, r)) == chess.Piece(chess.PAWN, color) for r in range(8))
            if not file_has_own_pawn:
                score -= 8
    heavy = sum(VAL[p.piece_type] for p in board.piece_map().values() if p.piece_type not in (chess.PAWN, chess.KING))
    if heavy > 20 and board.fullmove_number >= 8 and kf in (2, 3, 4, 5) and kr == (0 if color == chess.WHITE else 7):
        score -= 15      # still in the centre in the middlegame
    return max(0, min(100, score))


def mobility(board: chess.Board, color: bool) -> int:
    b = board.copy(stack=False)
    b.turn = color
    b.ep_square = None
    return sum(1 for _ in b.pseudo_legal_moves)


def control_heatmap(board: chess.Board) -> List[int]:
    """64 ints (a1..h8): white attackers minus black attackers. Positive = white controls the square."""
    return [len(board.attackers(chess.WHITE, s)) - len(board.attackers(chess.BLACK, s)) for s in chess.SQUARES]


def hanging_squares(board: chess.Board) -> List[Dict]:
    out = []
    for m in hanging(board):
        sq = m.squares[0]
        p = board.piece_at(chess.parse_square(sq))
        out.append({"square": sq, "color": chess.COLOR_NAMES[p.color], "piece": p.symbol().upper()})
    return out


def arrow(uci: Optional[str], color: str) -> Optional[Dict]:
    if not uci:
        return None
    return {"from": uci[:2], "to": uci[2:4], "color": color}


def position_visuals(board: chess.Board) -> Dict:
    return {
        "hanging": hanging_squares(board),
        "king_safety": {"white": king_safety(board, chess.WHITE), "black": king_safety(board, chess.BLACK)},
        "mobility": {"white": mobility(board, chess.WHITE), "black": mobility(board, chess.BLACK)},
        "heatmap": control_heatmap(board),
    }
