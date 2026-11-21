"""Whole-game review. Positions are analysed concurrently across the engine pool, but results are
yielded strictly in move order so the UI can animate the eval bar and badges as it streams."""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from typing import Any, AsyncIterator, Dict, List, Optional

import chess

from . import motifs as mt
from .classifier import ERRORS, ClassifierInput, MoveClass, classify, is_sacrifice
from .pool import EnginePool, PositionEval
from .visuals import arrow, position_visuals
from .winprob import MATE_CP, accuracy, win_prob

THREAT_DEPTH = 10


def phase_of(board: chess.Board, ply: int) -> str:
    npm = sum(mt.VAL[p.piece_type] for p in board.piece_map().values() if p.piece_type not in (chess.PAWN, chess.KING))
    if ply <= 20:
        return "opening"
    return "endgame" if npm <= 20 else "middlegame"


def _eval_dict(ev: PositionEval) -> Dict[str, Any]:
    mate = ev.best.mate if ev.best else None
    return {"cp": ev.cp_white, "mate": mate, "win": round(ev.win_white(), 4), "terminal": ev.terminal}


def _favours(mate: Optional[int], color: bool) -> bool:
    return mate is not None and ((mate > 0) == (color == chess.WHITE))


COMMENTS = {
    MoveClass.BRILLIANT: "Brilliant! A sound sacrifice that the engine endorses.",
    MoveClass.GREAT: "Great move: it was the only move that kept your position intact.",
    MoveClass.BEST: "Best move.",
    MoveClass.EXCELLENT: "Excellent, practically as good as the best move.",
    MoveClass.GOOD: "A good move.",
    MoveClass.INACCURACY: "An inaccuracy.",
    MoveClass.MISTAKE: "A mistake.",
    MoveClass.BLUNDER: "A blunder.",
    MoveClass.MISSED_WIN: "You missed a win here.",
}


def analyse_ply(ply: int, board_before: chess.Board, move: chess.Move, ev_before: PositionEval,
                ev_after: PositionEval, threat: Optional[PositionEval], clock: Optional[float]) -> Dict[str, Any]:
    """Pure function: turn engine output for one ply into the rich UI payload."""
    mover = board_before.turn
    san = board_before.san(move)
    after = board_before.copy(stack=False)
    after.push(move)

    best = ev_before.best
    best_uci = best.move_uci if best else move.uci()
    played_is_best = move.uci() == best_uci
    pov = (lambda cp: cp) if mover == chess.WHITE else (lambda cp: -cp)

    win_before = best.pov_win(mover) if best else win_prob(pov(ev_before.cp_white))
    win_after = win_prob(pov(ev_after.cp_white))
    loss = max(0.0, win_before - win_after) if not played_is_best else 0.0
    second = ev_before.lines[1].pov_win(mover) if len(ev_before.lines) > 1 else None

    best_is_mate = bool(best and _favours(best.mate, mover))
    after_best = ev_after.best
    keeps_mate = (_favours(after_best.mate, mover) if after_best else False) or (
        ev_after.terminal == "checkmate" and ev_after.cp_white * (1 if mover == chess.WHITE else -1) > 0)

    if played_is_best and best:
        line = best.pv_uci
    else:
        line = [move.uci()] + (after_best.pv_uci if after_best else [])
    sacrifice = is_sacrifice(board_before, line, mover)

    label = classify(ClassifierInput(win_before, win_after, played_is_best, second, sacrifice, best_is_mate, keeps_mate))
    acc = accuracy(loss)

    created = mt.move_creates(board_before, move)
    missed: List[mt.Motif] = []
    if not played_is_best and label in ERRORS and best:
        missed = mt.move_creates(board_before, chess.Move.from_uci(best_uci))
    missed_types = sorted({m.type for m in missed} | ({"mate"} if best_is_mate and label in ERRORS else set()))

    ww_before = ev_before.win_white()
    ww_after = ev_after.win_white()
    reasons = []
    if abs(ww_after - ww_before) >= 0.2:
        reasons.append("big_swing")
    if (ww_before - 0.5) * (ww_after - 0.5) < 0 and abs(ww_after - ww_before) >= 0.15:
        reasons.append("turning_point")
    if second is not None and win_before - second >= 0.15 and 0.35 <= win_before < 0.97:
        reasons.append("only_move")

    note = COMMENTS[label]
    if label in ERRORS and best:
        note += f" {best.move_san} was better (you lost {loss * 100:.0f}% win chance)."
        if missed_types:
            note += f" It would have created: {', '.join(t.replace('_', ' ') for t in missed_types)}."

    threat_arrow = None
    if threat and threat.best:
        threat_arrow = arrow(threat.best.move_uci, "red")

    vis = position_visuals(after)
    return {
        "ply": ply, "move_no": board_before.fullmove_number, "mover": chess.COLOR_NAMES[mover],
        "san": san, "uci": move.uci(), "fen_before": board_before.fen(), "fen_after": after.fen(),
        "label": label.value, "loss": round(loss, 4), "accuracy": round(acc, 1),
        "phase": phase_of(board_before, ply), "clock_left": clock,
        "eval_before": _eval_dict(ev_before), "eval_after": _eval_dict(ev_after),
        "best": {"san": best.move_san, "uci": best_uci, "pv_san": best.pv_san[:6]} if best else None,
        "lines": [{"rank": l.rank, "san": l.move_san, "uci": l.move_uci, "cp": l.cp, "mate": l.mate,
                   "win": round(l.pov_win(mover), 4), "pv_san": l.pv_san[:6]} for l in ev_before.lines],
        "arrows": {"best": arrow(best_uci, "green") if not played_is_best else None,
                   "played": arrow(move.uci(), "blue"), "threat": threat_arrow},
        "visuals": vis,
        "motifs": {"present": [m.to_dict() for m in mt.detect(after)],
                   "created": [m.to_dict() for m in created],
                   "missed": [m.to_dict() for m in missed]},
        "missed_motifs": missed_types,
        "critical": {"is_critical": bool(reasons), "reasons": reasons},
        "comment": note,
    }


def summarize(moves: List[Dict[str, Any]], evals: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"sides": {}, "critical": [], "key_moments": [], "eval": evals}
    for side in ("white", "black"):
        ms = [m for m in moves if m["mover"] == side]
        counts = Counter(m["label"] for m in ms)
        phases: Dict[str, List[float]] = defaultdict(list)
        for m in ms:
            phases[m["phase"]].append(m["accuracy"])
        out["sides"][side] = {
            "accuracy": round(sum(m["accuracy"] for m in ms) / len(ms), 1) if ms else None,
            "moves": len(ms), "counts": dict(counts),
            "phase_accuracy": {p: round(sum(v) / len(v), 1) for p, v in phases.items()},
        }
    out["critical"] = [{"ply": m["ply"], "reasons": m["critical"]["reasons"], "san": m["san"], "mover": m["mover"]}
                       for m in moves if m["critical"]["is_critical"]]
    out["key_moments"] = [m["ply"] for m in sorted((m for m in moves if m["label"] in {e.value for e in ERRORS}),
                                                   key=lambda m: -m["loss"])[:6]]
    return out


async def review_game(pool: EnginePool, start_fen: str, nodes: List[Dict[str, Any]], depth: int,
                      multipv: int) -> AsyncIterator[Dict[str, Any]]:
    """Yield {"type": "start"|"move"|"progress"|"summary"} events in order."""
    boards = [chess.Board(start_fen)]
    moves: List[chess.Move] = []
    for n in nodes:
        mv = chess.Move.from_uci(n["uci"])
        moves.append(mv)
        b = boards[-1].copy(stack=False)
        b.push(mv)
        boards.append(b)

    tasks = [asyncio.create_task(pool.analyse(b, depth, multipv)) for b in boards]
    threat_tasks: List[Optional[asyncio.Task]] = []
    for i, b in enumerate(boards[:-1]):
        if b.is_check():
            threat_tasks.append(None)
            continue
        nb = chess.Board(b.fen())            # "pass": same position, opponent to move (fresh board = no null-move history)
        nb.turn = not nb.turn
        nb.ep_square = None
        threat_tasks.append(asyncio.create_task(pool.analyse(nb, min(depth, THREAT_DEPTH), 1)))
    all_tasks = tasks + [t for t in threat_tasks if t]

    try:
        first = await tasks[0]
        evals = [_eval_dict(first)]
        yield {"type": "start", "fen": boards[0].fen(), "eval": evals[0], "total": len(moves)}
        results: List[Dict[str, Any]] = []
        for i, mv in enumerate(moves):
            ev_after = await tasks[i + 1]
            threat = await threat_tasks[i] if threat_tasks[i] else None
            ev_before = await tasks[i]
            data = analyse_ply(i + 1, boards[i], mv, ev_before, ev_after, threat, nodes[i].get("clock"))
            results.append(data)
            evals.append(_eval_dict(ev_after))
            yield {"type": "move", "move": data, "progress": {"done": i + 1, "total": len(moves)}}
        yield {"type": "summary", "summary": summarize(results, evals)}
    finally:
        for t in all_tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
