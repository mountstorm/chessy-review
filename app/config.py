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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    def resolved_stockfish(self) -> str:
        path = self.stockfish_path or os.environ.get("STOCKFISH") or shutil.which("stockfish")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
