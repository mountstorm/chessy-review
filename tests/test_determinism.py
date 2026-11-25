"""The same game must get the same ratings no matter which worker searches which position, or in what order."""
import asyncio
import random

import chess

from app.engine.pool import EnginePool, cache_key
from app.engine.review import review_game
from app.services.pgn import parse_pgn, mainline_nodes
from conftest import STOCKFISH, needs_engine

pytestmark = needs_engine

# Same game as the front end's "Try an example game".
EXAMPLE = ('1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 5. exd5 Nxd5 6. Nxf7 Kxf7 7. Qf3+ Ke6 8. Nc3 Nb4 9. Qe4 c6 '
           '10. a3 Na6 11. d4 Nac7 12. Bf4 1-0')


class ShuffledPool(EnginePool):
    """Delays each request by a seeded random amount, so positions reach the workers in a different order per seed."""

    def __init__(self, *a, seed: int, **kw):
        super().__init__(*a, **kw)
        self._rng = random.Random(seed)

    async def analyse(self, board, depth=14, multipv=3):
        await asyncio.sleep(self._rng.random() * 0.05)
        return await super().analyse(board, depth, multipv)


async def _review(seed: int, depth: int = 10, size: int = 3):
    pool = ShuffledPool(STOCKFISH, size, 16, seed=seed)       # fresh pool = fresh workers and an empty cache
    await pool.start()
    try:
        g = parse_pgn(EXAMPLE)[0]
        events = [e async for e in review_game(pool, g.tree["fen"], mainline_nodes(g.tree), depth, 3)]
    finally:
        await pool.stop()
    moves = [e["move"] for e in events if e["type"] == "move"]
    assert len(moves) == 23
    return [(m["ply"], m["label"], m["loss"], m["best"]["san"]) for m in moves]


async def test_review_is_identical_across_worker_orderings():
    runs = [await _review(seed) for seed in (1, 2, 3, 4)]
    for other in runs[1:]:
        assert other == runs[0]


async def test_every_worker_is_single_threaded(pool):
    assert len(pool._all) == 2
    for eng in pool._all:
        assert eng.config["Threads"] == 1


def test_cache_keys_are_versioned():
    assert cache_key(chess.Board(), 10, 3).startswith("v")
