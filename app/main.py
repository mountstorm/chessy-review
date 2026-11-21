from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import coach, games, players, positions
from .coach.llm import make_adapter
from .coach.tools import ToolExecutor
from .config import get_settings
from .engine.cache import make_cache
from .engine.pool import EnginePool
from .models.db import make_sessionmaker

log = logging.getLogger("chessy")
WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    app.state.settings = s
    app.state.cache = make_cache(s.redis_url, s.cache_size)
    app.state.pool = EnginePool(s.resolved_stockfish(), s.resolved_pool_size(), s.engine_hash_mb, app.state.cache)
    await app.state.pool.start()
    app.state.db_engine, app.state.sessionmaker = await make_sessionmaker(s.database_url)
    app.state.executor = ToolExecutor(app.state.pool)
    app.state.adapter = make_adapter(s)
    log.info("Chessy ready: %s x%d, coach=%s", app.state.pool.version, s.resolved_pool_size(), s.resolved_provider())
    try:
        yield
    finally:
        await app.state.pool.stop()
        await app.state.cache.close()
        await app.state.db_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Chessy", version="0.2.0", lifespan=lifespan,
                  description="Game review, what-if explorer and grounded AI coach.")
    origins = [o.strip() for o in get_settings().cors_origins.split(",")]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=origins != ["*"])
    for r in (games.router, positions.router, coach.router, players.router):
        app.include_router(r)

    @app.get("/api/health", tags=["meta"])
    async def health(request: Request):
        st = request.app.state
        return {"status": "ok", "engine": st.pool.version, "workers": st.pool.size,
                "coach": st.settings.resolved_provider(),
                "cache": {"hits": getattr(st.pool.cache, "hits", None), "misses": getattr(st.pool.cache, "misses", None)}}

    if WEB.exists():
        app.mount("/assets", StaticFiles(directory=WEB), name="assets")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(WEB / "index.html")

    return app


app = create_app()
