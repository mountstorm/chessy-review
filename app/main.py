from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import coach, games, masters, players, positions
from .coach.llm import make_adapter
from .coach.tools import ToolExecutor
from .config import get_settings
from .engine.cache import make_cache
from .engine.pool import EnginePool
from .engine.version import engine_version
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


def _hosted_state(app: FastAPI) -> None:
    """Serverless: no lifespan, no DB, no warm pool. Only the settings and the (per-instance) eval cache persist."""
    s = get_settings()
    app.state.settings = s
    app.state.cache = make_cache(None, s.cache_size)
    app.state.pool = None
    app.state.sessionmaker = None
    app.state.executor = None
    app.state.adapter = None


def create_app() -> FastAPI:
    hosted = get_settings().hosted
    app = FastAPI(title="Chessy", version="0.2.0", lifespan=None if hosted else lifespan,
                  description="Game review, what-if explorer and grounded AI coach.")
    if hosted:
        _hosted_state(app)
    origins = [o.strip() for o in get_settings().cors_origins.split(",")]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=origins != ["*"])
    @app.middleware("http")
    async def access_gate(request: Request, call_next):
        code = get_settings().access_code
        p = request.url.path
        if code and p.startswith("/api/") and p != "/api/health":
            if not hmac.compare_digest(request.headers.get("x-access-code", ""), code):
                return JSONResponse({"detail": "Access code required"}, status_code=401)
        return await call_next(request)

    for r in (games.router, positions.router, coach.router, players.router, masters.router):
        app.include_router(r)

    @app.get("/api/health", tags=["meta"])
    async def health(request: Request):
        st = request.app.state
        s = st.settings
        if s.hosted:
            try:
                engine = await engine_version(s.resolved_stockfish())
            except FileNotFoundError:
                engine = None                     # no binary: say so rather than guess
            return {"status": "ok", "hosted": True, "engine": engine, "workers": s.hosted_pool_size, "coach": "offline",
                    "needs_code": bool(s.access_code), "max_depth": s.hosted_max_depth, "max_plies": s.hosted_max_plies}
        return {"status": "ok", "hosted": False, "engine": st.pool.version, "workers": st.pool.size,
                "coach": s.resolved_provider(), "needs_code": bool(s.access_code),
                "cache": {"hits": getattr(st.pool.cache, "hits", None), "misses": getattr(st.pool.cache, "misses", None)}}

    if WEB.exists():
        app.mount("/assets", StaticFiles(directory=WEB), name="assets")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(WEB / "index.html")

    return app


app = create_app()
