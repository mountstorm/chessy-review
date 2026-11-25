"""The built-in example game and its precomputed review (app/data/example.json, built by tools/build_example.py)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "example.json"

EXAMPLE_PGN = ('[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n[Opening "Italian Game"]\n\n'
               '1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Ng5 d5 5. exd5 Nxd5 6. Nxf7 Kxf7 7. Qf3+ Ke6 8. Nc3 Nb4 9. Qe4 c6 '
               '10. a3 Na6 11. d4 Nac7 12. Bf4 1-0')


@lru_cache(maxsize=1)
def load_example() -> Dict[str, Any]:
    """{"depth", "multipv", "engine", "start", "moves", "summary"}: the same events a live review streams, as one document."""
    return json.loads(EXAMPLE_PATH.read_text())
