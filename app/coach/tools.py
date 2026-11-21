"""Engine-backed tools the LLM must use instead of guessing. Each returns plain JSON."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import chess
from pydantic import BaseModel, Field, ValidationError

from ..engine import motifs as mt
from ..engine.classifier import ClassifierInput, classify, is_sacrifice
from ..engine.pool import EnginePool
from ..engine.winprob import win_prob


class _Fen(BaseModel):
    fen: str


class EvaluateArgs(_Fen):
    depth: int = Field(14, ge=6, le=24)
    multipv: int = Field(3, ge=1, le=5)


class CandidateArgs(_Fen):
    move: str
    depth: int = Field(14, ge=6, le=24)


TOOL_SPECS: List[Dict[str, Any]] = [
    {"name": "evaluate_fen",
     "description": "Run Stockfish on a position. Returns the evaluation and the top lines (best move first) with "
                    "principal variations in SAN. Use this before making any claim about who is better or what to play.",
     "schema": {"type": "object", "properties": {
         "fen": {"type": "string", "description": "FEN of the position"},
         "depth": {"type": "integer", "description": "search depth, 6-24 (default 14)"},
         "multipv": {"type": "integer", "description": "number of lines, 1-5 (default 3)"}},
         "required": ["fen"], "additionalProperties": False}},
    {"name": "test_candidate_move",
     "description": "Check a specific move in a position: is it legal, how does it compare with the engine's best move, "
                    "how much win probability does it lose, and what is the engine's refutation? Use this to verify "
                    "any move you or the user suggests.",
     "schema": {"type": "object", "properties": {
         "fen": {"type": "string"}, "move": {"type": "string", "description": "SAN (Nf3) or UCI (g1f3)"},
         "depth": {"type": "integer"}},
         "required": ["fen", "move"], "additionalProperties": False}},
    {"name": "get_legal_moves",
     "description": "List every legal move in a position (SAN and UCI). Use to verify that a move exists.",
     "schema": {"type": "object", "properties": {"fen": {"type": "string"}}, "required": ["fen"],
                "additionalProperties": False}},
    {"name": "get_tactical_motifs",
     "description": "Detect tactical patterns on the board right now: hanging pieces, pins, skewers, forks, "
                    "discovered attacks.",
     "schema": {"type": "object", "properties": {"fen": {"type": "string"}}, "required": ["fen"],
                "additionalProperties": False}},
]


def parse_move(board: chess.Board, text: str) -> Optional[chess.Move]:
    t = text.strip()
    for fn in (board.parse_san, board.parse_uci):
        try:
            return fn(t)
        except ValueError:
            continue
    return None


def _board(fen: str) -> chess.Board:
    try:
        return chess.Board(fen)
    except ValueError as e:
        raise ValueError(f"invalid FEN: {e}")


class ToolExecutor:
    def __init__(self, pool: EnginePool):
        self.pool = pool

    async def run(self, name: str, args: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """Returns (result, is_error). Errors are data the model can read and recover from."""
        try:
            if name == "evaluate_fen":
                return await self.evaluate_fen(EvaluateArgs(**args)), False
            if name == "test_candidate_move":
                return await self.test_candidate_move(CandidateArgs(**args)), False
            if name == "get_legal_moves":
                return self.get_legal_moves(_Fen(**args).fen), False
            if name == "get_tactical_motifs":
                return self.get_tactical_motifs(_Fen(**args).fen), False
            return {"error": f"unknown tool '{name}'"}, True
        except ValidationError as e:
            return {"error": f"bad arguments: {e.errors()[0]['msg']} ({e.errors()[0]['loc']})"}, True
        except ValueError as e:
            return {"error": str(e)}, True

    async def evaluate_fen(self, a: EvaluateArgs) -> Dict[str, Any]:
        b = _board(a.fen)
        ev = await self.pool.analyse(b, a.depth, a.multipv)
        stm = b.turn
        return {
            "fen": b.fen(), "side_to_move": chess.COLOR_NAMES[stm], "in_check": b.is_check(),
            "terminal": ev.terminal, "depth": ev.depth,
            "eval_cp_white": ev.cp_white, "win_prob_white": round(ev.win_white(), 3),
            "lines": [{"rank": l.rank, "move": l.move_san, "uci": l.move_uci, "cp_white": l.cp, "mate": l.mate,
                       "win_prob_side_to_move": round(l.pov_win(stm), 3), "pv": l.pv_san[:8]} for l in ev.lines],
        }

    async def test_candidate_move(self, a: CandidateArgs) -> Dict[str, Any]:
        b = _board(a.fen)
        mv = parse_move(b, a.move)
        if mv is None:
            legal = ", ".join(b.san(m) for m in b.legal_moves)
            return {"legal": False, "error": f"'{a.move}' is not legal here", "legal_moves": legal}
        mover = b.turn
        before = await self.pool.analyse(b, a.depth, 3)
        san = b.san(mv)
        created = mt.move_creates(b, mv)
        after_b = b.copy(stack=False)
        after_b.push(mv)
        after = await self.pool.analyse(after_b, a.depth, 1)
        best = before.best
        win_before = best.pov_win(mover) if best else 0.5
        win_after = win_prob(after.cp_white if mover == chess.WHITE else -after.cp_white)
        is_best = bool(best and best.move_uci == mv.uci())
        rank = next((l.rank for l in before.lines if l.move_uci == mv.uci()), None)
        ab = after.best
        cls = classify(ClassifierInput(win_before, win_after, is_best,
                                       before.lines[1].pov_win(mover) if len(before.lines) > 1 else None,
                                       is_sacrifice(b, [mv.uci()] + (ab.pv_uci if ab else []), mover)))
        return {
            "legal": True, "move": san, "uci": mv.uci(), "is_engine_best": is_best, "engine_rank": rank,
            "classification": cls.value, "win_prob_loss": round(max(0.0, win_before - win_after), 3),
            "best_move": best.move_san if best else None,
            "best_line": best.pv_san[:6] if best else [],
            "eval_after_cp_white": after.cp_white,
            "opponent_best_reply": ab.move_san if ab else None,
            "refutation_line": ab.pv_san[:6] if ab else [],
            "motifs_created": [m.to_dict() for m in created],
            "gives_check": b.gives_check(mv), "is_capture": b.is_capture(mv),
        }

    def get_legal_moves(self, fen: str) -> Dict[str, Any]:
        b = _board(fen)
        moves = [{"san": b.san(m), "uci": m.uci(), "piece": chess.piece_name(b.piece_type_at(m.from_square)),
                  "capture": b.is_capture(m), "check": b.gives_check(m)} for m in b.legal_moves]
        return {"side_to_move": chess.COLOR_NAMES[b.turn], "count": len(moves), "moves": moves}

    def get_tactical_motifs(self, fen: str) -> Dict[str, Any]:
        b = _board(fen)
        ms = mt.detect(b)
        return {"count": len(ms), "motifs": [m.to_dict() for m in ms]}
