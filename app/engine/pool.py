"""Async Stockfish worker pool. Each worker is its own UCI process driven through
python-chess's asyncio transport, so concurrent requests never block the event loop."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import chess
import chess.engine

from .cache import Cache, MemoryCache
from .winprob import MATE_CP, win_prob

log = logging.getLogger("chessy.engine")


@dataclass
class Line:
    rank: int
    move_uci: str
    move_san: str
    cp: int                      # white POV, mates folded to +/-MATE_CP
    mate: Optional[int]          # white POV: +3 = white mates in 3, -2 = black mates in 2
    pv_uci: List[str]
    pv_san: List[str]

    def pov_cp(self, color: bool) -> int:
        return self.cp if color == chess.WHITE else -self.cp

    def pov_win(self, color: bool) -> float:
        return win_prob(self.pov_cp(color))


@dataclass
class PositionEval:
    fen: str
    depth: int
    multipv: int
    lines: List[Line] = field(default_factory=list)
    terminal: Optional[str] = None       # "checkmate" | "stalemate" | "draw" | None
    cp_white: int = 0                    # eval of the position (best line), white POV

    @property
    def best(self) -> Optional[Line]:
        return self.lines[0] if self.lines else None

    def win_white(self) -> float:
        return win_prob(self.cp_white)

    def to_dict(self) -> dict:
        return {
            "fen": self.fen, "depth": self.depth, "multipv": self.multipv,
            "terminal": self.terminal, "cp_white": self.cp_white,
            "lines": [vars(l) for l in self.lines],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PositionEval":
        return cls(d["fen"], d["depth"], d["multipv"], [Line(**l) for l in d["lines"]], d["terminal"], d["cp_white"])


def cache_key(board: chess.Board, depth: int, multipv: int) -> str:
    return f"{board.epd()}|{depth}|{multipv}"


def _terminal_eval(board: chess.Board, depth: int, multipv: int) -> PositionEval:
    if board.is_checkmate():
        cp = -MATE_CP if board.turn == chess.WHITE else MATE_CP
        return PositionEval(board.fen(), depth, multipv, [], "checkmate", cp)
    kind = "stalemate" if board.is_stalemate() else "draw"
    return PositionEval(board.fen(), depth, multipv, [], kind, 0)


class EnginePool:
    def __init__(self, path: str, size: int = 2, hash_mb: int = 64, cache: Optional[Cache] = None):
        self.path, self.size, self.hash_mb = path, size, hash_mb
        self.cache: Cache = cache or MemoryCache()
        self._free: "asyncio.Queue[chess.engine.UciProtocol]" = asyncio.Queue()
        self._all: list = []
        self.version = ""
        self._inflight: dict[str, asyncio.Future] = {}
        self.searches = 0                      # engine searches actually run (cache hits/coalesced calls excluded)

    async def start(self) -> None:
        for _ in range(self.size):
            await self._spawn()
        self.version = self._all[0].id.get("name", "Stockfish") if self._all else ""

    async def _spawn(self):
        _, eng = await chess.engine.popen_uci(self.path)
        try:
            await eng.configure({"Threads": 1, "Hash": self.hash_mb})
        except chess.engine.EngineError:
            pass
        self._all.append(eng)
        self._free.put_nowait(eng)
        return eng

    async def stop(self) -> None:
        for eng in self._all:
            try:
                await asyncio.wait_for(eng.quit(), 3)
            except Exception:
                pass
        self._all.clear()

    async def analyse(self, board: chess.Board, depth: int = 14, multipv: int = 3) -> PositionEval:
        if board.is_game_over(claim_draw=False) or not any(board.legal_moves):
            return _terminal_eval(board, depth, multipv)
        key = cache_key(board, depth, multipv)
        hit = await self.cache.get(key)
        if hit:
            return PositionEval.from_dict(hit)
        # coalesce identical concurrent requests
        if key in self._inflight:
            return await self._inflight[key]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            result = await self._run(board, depth, multipv)
            await self.cache.set(key, result.to_dict())
            fut.set_result(result)
            return result
        except BaseException as e:
            fut.set_exception(e) if not fut.done() else None
            fut.exception()  # mark retrieved so asyncio doesn't warn
            raise
        finally:
            self._inflight.pop(key, None)

    async def _run(self, board: chess.Board, depth: int, multipv: int) -> PositionEval:
        eng = await self._free.get()
        self.searches += 1
        crashed = False
        try:
            mpv = max(1, min(multipv, board.legal_moves.count()))
            infos = await eng.analyse(board, chess.engine.Limit(depth=depth), multipv=mpv)
        except (chess.engine.EngineTerminatedError, chess.engine.EngineError):
            crashed = True
            raise
        finally:
            if crashed:
                log.warning("engine worker died; respawning")
                self._all.remove(eng)
                try:
                    await self._spawn()
                except Exception:
                    log.exception("respawn failed")
            else:
                self._free.put_nowait(eng)
        if isinstance(infos, dict):
            infos = [infos]
        lines = []
        for i, info in enumerate(infos):
            pv = info.get("pv") or []
            if not pv:
                continue
            sc = info["score"].white()
            b = board.copy()
            sans = []
            for m in pv[:10]:
                sans.append(b.san(m))
                b.push(m)
            lines.append(Line(i + 1, pv[0].uci(), sans[0], sc.score(mate_score=MATE_CP), sc.mate(),
                              [m.uci() for m in pv[:10]], sans))
        cp = lines[0].cp if lines else 0
        return PositionEval(board.fen(), depth, multipv, lines, None, cp)
