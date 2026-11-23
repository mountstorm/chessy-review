"""Tactical motif detection with plain python-chess geometry: hanging pieces, pins, skewers,
forks and discovered attacks. Detection is about *patterns on the board now*; `move_creates`
reports which patterns a given move brings about for the mover."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Tuple

import chess

VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
SLIDERS = (chess.BISHOP, chess.ROOK, chess.QUEEN)


@dataclass(frozen=True)
class Motif:
    type: str                 # hanging | pin | skewer | fork | discovered_attack
    side: str                 # who benefits: "white" | "black"
    squares: tuple            # key squares, e.g. (attacker, target, ...)
    description: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["squares"] = list(self.squares)
        return d

    @property
    def key(self):
        return (self.type, self.side, self.squares)


def _name(board: chess.Board, sq: int) -> str:
    p = board.piece_at(sq)
    return f"{chess.COLOR_NAMES[p.color]} {chess.piece_name(p.piece_type)} on {chess.square_name(sq)}"


def _side(color: bool) -> str:
    return "white" if color == chess.WHITE else "black"


def hanging_reasons(board: chess.Board) -> List[Tuple[int, str]]:
    """(square, why) for every piece that can be won: attacked by the enemy and undefended, or attacked by something
    cheaper. A plain attack/defence count; it does not look at what the engine thinks happens next."""
    out = []
    for sq, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue
        enemy = not piece.color
        attackers = board.attackers(enemy, sq)
        if not attackers:
            continue
        defenders = board.attackers(piece.color, sq)
        cheapest = min(VAL[board.piece_at(a).piece_type] for a in attackers)
        if not defenders:
            out.append((sq, "undefended"))
        elif cheapest < VAL[piece.piece_type]:
            out.append((sq, "by a cheaper piece"))
    return out


def hanging(board: chess.Board) -> List[Motif]:
    """Pieces that can be won: attacked by the enemy and undefended, or attacked by something cheaper."""
    return [Motif("hanging", _side(not board.piece_at(sq).color), (chess.square_name(sq),),
                  f"{_name(board, sq)} can be captured ({why})")
            for sq, why in hanging_reasons(board)]


def _line_behind(board: chess.Board, slider: int, front: int) -> List[int]:
    """Squares newly seen by `slider` once the piece on `front` is removed (the x-ray behind it)."""
    b = board.copy(stack=False)
    before = b.attacks(slider)
    b.remove_piece_at(front)
    return [s for s in b.attacks(slider) if s not in before]


def pins_and_skewers(board: chess.Board) -> List[Motif]:
    out = []
    for color in (chess.WHITE, chess.BLACK):
        for s in board.pieces(chess.BISHOP, color) | board.pieces(chess.ROOK, color) | board.pieces(chess.QUEEN, color):
            for front in board.attacks(s):
                fp = board.piece_at(front)
                if not fp or fp.color == color:
                    continue
                for back in _line_behind(board, s, front):
                    bp = board.piece_at(back)
                    if not bp or bp.color != fp.color:
                        continue
                    # confirm the three squares are collinear with the slider (x-ray, not a side effect)
                    if back not in chess.SquareSet(chess.ray(s, front)):
                        continue
                    if VAL[bp.piece_type] > VAL[fp.piece_type]:
                        absolute = bp.piece_type == chess.KING
                        out.append(Motif("pin", _side(color), (chess.square_name(s), chess.square_name(front), chess.square_name(back)),
                                         f"{_name(board, front)} is {'pinned to the king' if absolute else 'pinned to ' + _name(board, back)} "
                                         f"by {_name(board, s)}"))
                    elif VAL[fp.piece_type] > VAL[bp.piece_type] and VAL[fp.piece_type] >= 5:
                        out.append(Motif("skewer", _side(color), (chess.square_name(s), chess.square_name(front), chess.square_name(back)),
                                         f"{_name(board, s)} skewers {_name(board, front)} with {_name(board, back)} behind it"))
    return out


def forks(board: chess.Board) -> List[Motif]:
    out = []
    for color in (chess.WHITE, chess.BLACK):
        for sq, piece in board.piece_map().items():
            if piece.color != color:
                continue
            targets = []
            for t in board.attacks(sq):
                tp = board.piece_at(t)
                if not tp or tp.color == color:
                    continue
                valuable = VAL[tp.piece_type] > VAL[piece.piece_type] or tp.piece_type == chess.KING
                loose = not board.attackers(tp.color, t) and tp.piece_type != chess.PAWN
                if valuable or loose:
                    targets.append(t)
            if len(targets) >= 2 and piece.piece_type != chess.KING:
                names = ", ".join(_name(board, t) for t in targets)
                out.append(Motif("fork", _side(color), tuple([chess.square_name(sq)] + [chess.square_name(t) for t in targets]),
                                 f"{_name(board, sq)} forks {names}"))
    return out


def discovered(board: chess.Board) -> List[Motif]:
    """A friendly piece standing in front of its own slider; moving it uncovers an attack on a real target."""
    out = []
    for color in (chess.WHITE, chess.BLACK):
        for s in board.pieces(chess.BISHOP, color) | board.pieces(chess.ROOK, color) | board.pieces(chess.QUEEN, color):
            for front in board.attacks(s):
                fp = board.piece_at(front)
                if not fp or fp.color != color or front == s:
                    continue
                for back in _line_behind(board, s, front):
                    bp = board.piece_at(back)
                    if bp and bp.color != color and (VAL[bp.piece_type] >= 3) and back in chess.SquareSet(chess.ray(s, front)):
                        out.append(Motif("discovered_attack", _side(color),
                                         (chess.square_name(s), chess.square_name(front), chess.square_name(back)),
                                         f"moving {_name(board, front)} uncovers {_name(board, s)} on {_name(board, back)}"))
    return out


def detect(board: chess.Board) -> List[Motif]:
    return hanging(board) + pins_and_skewers(board) + forks(board) + discovered(board)


def move_creates(board: chess.Board, move: chess.Move) -> List[Motif]:
    """Motifs that exist for the mover *after* the move and did not exist before it (plus captures of loose pieces)."""
    mover = board.turn
    before = {m.key for m in detect(board) if m.side == _side(mover)}
    loose_victim = None
    if board.is_capture(move):
        for m in hanging(board):
            if m.side == _side(mover) and m.squares == (chess.square_name(move.to_square),):
                loose_victim = m
    after_board = board.copy(stack=False)
    after_board.push(move)
    created = [m for m in detect(after_board) if m.side == _side(mover) and m.key not in before]
    if loose_victim:
        created.append(loose_victim)
    if after_board.is_checkmate():
        created.append(Motif("mate", _side(mover), (chess.square_name(move.to_square),), "delivers checkmate"))
    return created
