"""Where engines come from. Locally: one long-lived pool owned by the app. Hosted (serverless): the platform keeps no
warm process or event loop between requests, so each request spawns its own small pool and tears it down afterwards."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from .pool import EnginePool


@asynccontextmanager
async def engine_scope(app: FastAPI) -> AsyncIterator[EnginePool]:
    st = app.state
    if not st.settings.hosted:
        yield st.pool
        return
    pool = EnginePool(st.settings.resolved_stockfish(), st.settings.hosted_pool_size, 32, st.cache)
    await pool.start()
    try:
        yield pool
    finally:
        await pool.stop()
