import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

STOCKFISH = shutil.which("stockfish")
needs_engine = pytest.mark.skipif(STOCKFISH is None, reason="stockfish not installed")


@pytest.fixture(scope="session", autouse=True)
def _env():
    tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp}/test.db"
    os.environ["ENGINE_POOL_SIZE"] = "2"
    os.environ["LLM_PROVIDER"] = "offline"
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
async def pool():
    from app.engine.pool import EnginePool
    p = EnginePool(STOCKFISH, 2, 16)
    await p.start()
    yield p
    await p.stop()


def parse_sse(text: str):
    events = []
    for block in text.strip().split("\n\n"):
        ev, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            events.append((ev, data))
    return events
