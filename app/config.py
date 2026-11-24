from __future__ import annotations

import os
import shutil
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def local_server_up(base_url: str, timeout: float = 0.6) -> bool:
    """True if an OpenAI-compatible server answers at base_url (e.g. Ollama on :11434)."""
    import httpx
    try:
        return httpx.get(base_url.rstrip("/") + "/models", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # engine
    stockfish_path: Optional[str] = None
    engine_pool_size: int = 0           # 0 = auto (half the cores, 1..4)
    engine_hash_mb: int = 64
    default_depth: int = 14
    max_depth: int = 24

    # persistence / cache
    database_url: str = "sqlite+aiosqlite:///./chessy.db"
    redis_url: Optional[str] = None     # optional; in-memory LRU is used when unset
    cache_size: int = 50_000

    # coach LLM: "auto" picks Anthropic, then OpenAI, then the offline engine-grounded coach
    llm_provider: str = "auto"          # auto | anthropic | openai | local | offline
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "low"       # low keeps the coach snappy; raise for deeper answers
    anthropic_refusal_fallback: bool = True
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4.1"
    # local model: any OpenAI-compatible server on your machine (Ollama, LM Studio, llama.cpp). No key, no network.
    local_llm_url: str = "http://localhost:11434/v1"
    local_llm_model: str = "qwen2.5:3b"
    coach_max_tool_rounds: int = 6

    cors_origins: str = "*"

    # hosted / serverless mode (Vercel): stateless, no database, engines spawned per request, offline coach only
    hosted: bool = False                # env CHESSY_HOSTED is mapped below
    access_code: Optional[str] = None   # if set, /api/* (except health) requires header X-Access-Code
    hosted_pool_size: int = 2
    hosted_max_depth: int = 14
    hosted_max_plies: int = 200
    # per-IP rate limits in hosted mode, requests per window (0 = unlimited); env HOSTED_RATE_ANALYZE etc.
    hosted_rate_analyze: int = 10
    hosted_rate_evaluate: int = 120
    hosted_rate_coach: int = 30
    hosted_rate_window: int = 3600      # seconds
    # shared rate-limit counters (Upstash Redis REST); unset = per-instance in-memory counts
    upstash_redis_rest_url: Optional[str] = None
    upstash_redis_rest_token: Optional[str] = None

    def resolved_stockfish(self) -> str:
        path = self.stockfish_path or os.environ.get("STOCKFISH") or shutil.which("stockfish") or _vendored_stockfish()
        if not path:
            raise FileNotFoundError(
                "Stockfish not found. Run ./setup.sh, `brew install stockfish` / `sudo apt install stockfish`, "
                "or set STOCKFISH_PATH in .env."
            )
        return path

    def resolved_pool_size(self) -> int:
        if self.engine_pool_size > 0:
            return self.engine_pool_size
        return max(1, min(4, (os.cpu_count() or 2) // 2))

    def resolved_provider(self) -> str:
        if self.hosted:
            return "offline"            # no LLM and no local model server on serverless
        p = self.llm_provider.lower()
        if p != "auto":
            return p
        if self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if self.openai_api_key or os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if local_server_up(self.local_llm_url):
            return "local"
        return "offline"


def _vendored_stockfish() -> Optional[str]:
    """Linux x86-64 binary shipped in vendor/ (see scripts/fetch_stockfish_linux.sh). Serverless file systems are
    read-only and may drop the exec bit, so it is copied to /tmp once per instance."""
    import platform
    import stat
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "stockfish-linux-x86-64")
    if not os.path.exists(src) or platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        return None
    dst = "/tmp/stockfish-linux-x86-64"
    if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
        shutil.copyfile(src, dst)
        os.chmod(dst, os.stat(dst).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dst


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Vercel always sets VERCEL=1, so hosted mode works whichever file the platform picks as the entry point
    if os.environ.get("CHESSY_HOSTED", "").lower() in ("1", "true", "yes") or os.environ.get("VERCEL"):
        s.hosted = True
    if os.environ.get("CHESSY_ACCESS_CODE"):
        s.access_code = os.environ["CHESSY_ACCESS_CODE"]
    return s
