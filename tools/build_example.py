"""Regenerate app/data/example.json: a depth-18 review of the example game, in the shape the analyze API streams.

    .venv/bin/python tools/build_example.py [--depth 18] [--out app/data/example.json]

Takes about a minute. Run it again whenever the analysis output changes (tests/test_example.py tells you when)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.coach.offline import with_moments                      # noqa: E402
from app.config import get_settings                             # noqa: E402
from app.engine.pool import EnginePool                          # noqa: E402
from app.engine.review import review_game                      # noqa: E402
from app.services import pgn as pgnlib                          # noqa: E402
from app.services.example import EXAMPLE_PATH, EXAMPLE_PGN      # noqa: E402

MULTIPV = 3


async def build(depth: int) -> dict:
    s = get_settings()
    pool = EnginePool(s.resolved_stockfish(), s.resolved_pool_size(), s.engine_hash_mb)
    await pool.start()
    try:
        game = pgnlib.parse_pgn(EXAMPLE_PGN)[0]
        start, moves, summary = None, [], None
        async for ev in review_game(pool, game.tree["fen"], pgnlib.mainline_nodes(game.tree), depth, MULTIPV):
            if ev["type"] == "start":
                start = {k: ev[k] for k in ("fen", "eval", "total")}
            elif ev["type"] == "move":
                moves.append(ev["move"])
            elif ev["type"] == "summary":
                summary = with_moments(ev["summary"], moves)
        return {"depth": depth, "multipv": MULTIPV, "engine": pool.version, "start": start, "moves": moves, "summary": summary}
    finally:
        await pool.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--depth", type=int, default=18)
    ap.add_argument("--out", type=Path, default=EXAMPLE_PATH)
    a = ap.parse_args()
    data = asyncio.run(build(a.depth))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {a.out} ({len(data['moves'])} moves, depth {a.depth}, {data['engine']})")


if __name__ == "__main__":
    main()
