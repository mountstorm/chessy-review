"""Move classification from expected-points (win-probability) change, not raw centipawn spikes.

Tiers (expected-points loss L = best_win - played_win, from the mover's side):
  best-or-equal   L <= 0.005   -> Brilliant (sound sacrifice) / Great (only move) / Best
  Excellent       L <= 0.02
  Good            L <= 0.05
  Inaccuracy      L <= 0.10
  Mistake         L <= 0.20
  Blunder         L  > 0.20
  Missed Win      you were winning, the move dropped >= 0.15 but you are not lost
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

import chess

from .motifs import VAL


class MoveClass(str, Enum):
    BRILLIANT = "brilliant"
    GREAT = "great"
    BEST = "best"
    EXCELLENT = "excellent"
    GOOD = "good"
    INACCURACY = "inaccuracy"
    MISTAKE = "mistake"
    BLUNDER = "blunder"
    MISSED_WIN = "missed_win"


ERRORS = {MoveClass.INACCURACY, MoveClass.MISTAKE, MoveClass.BLUNDER, MoveClass.MISSED_WIN}


@dataclass
class ClassifierInput:
    win_before: float                 # mover's win prob if they play the best move
    win_after: float                  # mover's win prob after the move actually played
    played_is_best: bool = False
    second_best_win: Optional[float] = None   # win prob of the engine's 2nd choice (mover POV), if any
    sacrifice: bool = False
    best_is_mate: bool = False
    keeps_mate: bool = False


def classify(i: ClassifierInput) -> MoveClass:
    loss = max(0.0, i.win_before - i.win_after)
    if i.played_is_best or loss <= 0.005:
        if i.sacrifice and i.win_after >= 0.45 and i.win_before <= 0.92:
            return MoveClass.BRILLIANT
        if (i.second_best_win is not None and i.win_before - i.second_best_win >= 0.15
                and i.win_before < 0.97 and i.win_before >= 0.2):
            return MoveClass.GREAT
        return MoveClass.BEST
    if loss <= 0.02:
        if i.sacrifice and i.win_after >= 0.45 and i.win_before <= 0.92:
            return MoveClass.BRILLIANT
        return MoveClass.EXCELLENT
    if loss <= 0.05:
        return MoveClass.GOOD
    if loss <= 0.10:
        return MoveClass.INACCURACY
    cls = MoveClass.MISTAKE if loss <= 0.20 else MoveClass.BLUNDER
    if i.best_is_mate and not i.keeps_mate and loss >= 0.10 and i.win_after >= 0.35:
        return MoveClass.MISSED_WIN
    if i.win_before >= 0.75 and loss >= 0.15 and 0.35 <= i.win_after < 0.65:
        return MoveClass.MISSED_WIN
    return cls


def material(board: chess.Board, color: bool) -> int:
    return sum(VAL[p.piece_type] for p in board.piece_map().values() if p.color == color and p.piece_type != chess.KING)


def is_sacrifice(board: chess.Board, line_uci: Sequence[str], mover: bool) -> bool:
    """True when the mover's net material is down >= 2 after the reply AND still down after up to 4 plies,
    i.e. material is given up and not simply traded back."""
    if len(line_uci) < 2:
        return False
    b = board.copy(stack=False)
    base = material(b, mover) - material(b, not mover)
    swings = []
    for k, u in enumerate(line_uci[:4]):
        try:
            b.push_uci(u)
        except ValueError:
            return False
        swings.append(material(b, mover) - material(b, not mover) - base)
    first = board.piece_at(chess.Move.from_uci(line_uci[0]).from_square)
    if first is None or first.piece_type == chess.PAWN:
        return False
    return swings[1] <= -2 and swings[-1] <= -2
