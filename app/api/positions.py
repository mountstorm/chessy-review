from __future__ import annotations

from typing import Any, Dict

import chess
from fastapi import APIRouter, HTTPException, Request

from ..coach.tools import parse_move
from ..engine import motifs as mt
from ..engine.visuals import arrow, position_visuals
from ..models.schemas import EvaluateRequest

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.post("/evaluate")
async def evaluate(body: EvaluateRequest, request: Request) -> Dict[str, Any]:
    """Branch explorer: any FEN plus an optional move sequence -> MultiPV lines, win %, motifs, overlay data.
    Repeated lookups are served from the (FEN, depth, multipv) cache."""
    st = request.app.state
    board = chess.Board(body.fen)
    played = []
    for t in body.moves:
        mv = parse_move(board, t)
        if mv is None:
            raise HTTPException(422, f"Illegal or unreadable move '{t}' at ply {len(played) + 1} "
                                     f"(legal: {', '.join(board.san(m) for m in board.legal_moves)})")
        played.append(board.san(mv))
        board.push(mv)
    depth = min(body.depth, st.settings.max_depth)
    hits_before = getattr(st.pool.cache, "hits", 0)
    ev = await st.pool.analyse(board, depth, body.multipv)
    stm = board.turn
    return {
        "fen": board.fen(), "played": played, "side_to_move": chess.COLOR_NAMES[stm], "in_check": board.is_check(),
        "terminal": ev.terminal, "depth": ev.depth, "cached": getattr(st.pool.cache, "hits", 0) > hits_before,
        "eval": {"cp_white": ev.cp_white, "mate": ev.best.mate if ev.best else None, "win_white": round(ev.win_white(), 4)},
        "lines": [{"rank": l.rank, "san": l.move_san, "uci": l.move_uci, "cp_white": l.cp, "mate": l.mate,
                   "win_side_to_move": round(l.pov_win(stm), 4), "win_white": round(l.pov_win(chess.WHITE), 4),
                   "pv_san": l.pv_san, "pv_uci": l.pv_uci} for l in ev.lines],
        "arrows": {"best": arrow(ev.best.move_uci, "green") if ev.best else None,
                   "alternatives": [arrow(l.move_uci, "blue") for l in ev.lines[1:3]]},
        "motifs": [m.to_dict() for m in mt.detect(board)],
        "visuals": position_visuals(board),
        "legal_moves": [board.san(m) for m in board.legal_moves],
        "legal_uci": [m.uci() for m in board.legal_moves],
    }


@router.get("/legal")
async def legal(fen: str) -> Dict[str, Any]:
    """Engine-free legal-move lookup so the board can offer click-to-move instantly."""
    try:
        board = chess.Board(fen)
    except ValueError as e:
        raise HTTPException(422, f"Invalid FEN: {e}")
    return {"fen": board.fen(), "turn": chess.COLOR_NAMES[board.turn], "legal_uci": [m.uci() for m in board.legal_moves],
            "terminal": board.is_game_over()}
