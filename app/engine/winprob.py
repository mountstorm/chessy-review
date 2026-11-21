"""Win-probability / expected-points math. Classification works on these, never raw centipawns."""
from __future__ import annotations

import math

MATE_CP = 10_000


def win_prob(cp: float) -> float:
    """Lichess's logistic curve: centipawns (side's own POV) -> expected score in [0, 1]."""
    return 0.5 + 0.5 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def accuracy(loss: float) -> float:
    """Per-move accuracy (0..100) from expected-points loss (0..1). Lichess's formula, on 0-100 win%."""
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * (loss * 100)) - 3.1669))


def cp_to_str(cp: int, mate: int | None = None) -> str:
    if mate is not None:
        return f"{'-' if mate < 0 else ''}M{abs(mate)}"
    return f"{cp / 100:+.2f}"
