"""Ask a UCI engine binary for its name/version ("Stockfish 17.1"), once per binary, without starting a pool."""
from __future__ import annotations

import asyncio
from typing import Dict, Optional

_cache: Dict[str, str] = {}


async def engine_version(path: str, timeout: float = 5.0) -> Optional[str]:
    """The `id name` line the engine reports for the `uci` command, or None if it can't be run.
    Only successes are cached; a missing or hung binary is retried on the next call (bounded by `timeout`)."""
    if path in _cache:
        return _cache[path]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            path, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

        async def read() -> Optional[str]:
            proc.stdin.write(b"uci\n")
            await proc.stdin.drain()
            name = None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if line.startswith("id name "):
                    name = line[len("id name "):].strip()
                elif line == "uciok":
                    return name
            return name

        name = await asyncio.wait_for(read(), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
    if name:
        _cache[path] = name
    return name or None
