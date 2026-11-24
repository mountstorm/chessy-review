"""Offline coach: no LLM, no key. Every sentence is built from engine output, so it is grounded by construction.
Used when no API key is configured (or the provider fails), and it speaks the same event protocol as the LLM path."""
from __future__ import annotations

import re
from typing import Any, AsyncIterator, Dict, List, Optional

import chess

from ..engine import motifs as mt
from ..engine.winprob import win_prob
from .actions import UiActions
from .tools import ToolExecutor, parse_move

PIECE_NAMES = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop", chess.ROOK: "rook",
               chess.QUEEN: "queen", chess.KING: "king"}
LABEL_TEXT = {"brilliant": "a brilliant move", "great": "a great move", "best": "the best move", "excellent": "excellent",
              "good": "a good move", "inaccuracy": "an inaccuracy", "mistake": "a mistake", "blunder": "a blunder",
              "missed_win": "a missed win"}


# What the better move would have set up, in words (the review records motif types for moves it says were missed).
# The order is the order of importance: when there are more than two ideas, the first two are the ones mentioned.
MOTIF_PHRASE = {"mate": "a forced mate", "fork": "a fork", "pin": "a pin", "skewer": "a skewer",
                "discovered_attack": "a discovered attack", "hanging": "a loose piece to win"}
MOMENT_MIN_LOSS = 0.15      # a "turning point" costs at least this much win probability
MOMENT_LIMIT = 3


def moment_sentence(move: Dict[str, Any]) -> str:
    """One third-person sentence about a costly move, from the engine's review data only. Third person on purpose: the same
    line is shown for either player, so it can't say "you"."""
    side = move["mover"].capitalize()
    cost = f"{side}'s {move['san']} " + ("let a win slip away, costing" if move["label"] == "missed_win" else "cost") + \
        f" {round(move['loss'] * 100)}% win chance"
    best = (move.get("best") or {}).get("san")
    if not best:
        return cost + "."
    tail = f"{best} was the better move"
    ideas = [MOTIF_PHRASE[t] for t in MOTIF_PHRASE if t in (move.get("missed_motifs") or [])][:2]          # importance order, not input order
    if ideas:
        tail += ", setting up " + " and ".join(ideas)
    return f"{cost}; {tail}."


def build_moments(moves: List[Dict[str, Any]], key_moments: List[int]) -> List[Dict[str, Any]]:
    """The turning points of a game: from the review's key moments (its biggest losses), keep the ones that really cost
    something, at most MOMENT_LIMIT, and put them in game order so they read as the story of the game.

    When none is big enough, fall back to the biggest slips among the key moments, flagged "minor", so this list is never
    empty while the review's own key moments list isn't (the two would contradict each other)."""
    by_ply = {m["ply"]: m for m in moves}
    known = [by_ply[p] for p in key_moments if p in by_ply]
    picked = [m for m in known if m["loss"] >= MOMENT_MIN_LOSS][:MOMENT_LIMIT]
    minor = not picked
    if minor:
        picked = known[:MOMENT_LIMIT]
    return [{"ply": m["ply"], "sentence": moment_sentence(m), **({"minor": True} if minor else {})}
            for m in sorted(picked, key=lambda m: m["ply"])]


def with_moments(summary: Dict[str, Any], moves: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The summary plus its turning points. Reviews saved before this existed don't have them, so fill them in on the way out."""
    if summary.get("moments") or (not summary.get("key_moments") and "moments" in summary):
        return summary                  # an empty list saved next to key moments is repaired below
    return {**summary, "moments": build_moments(moves, summary.get("key_moments") or [])}


def _standing(win_stm: float, side: str) -> str:
    other = "Black" if side == "white" else "White"
    me = side.capitalize()
    if win_stm >= 0.9: return f"{me} is winning"
    if win_stm >= 0.7: return f"{me} is clearly better"
    if win_stm >= 0.57: return f"{me} is slightly better"
    if win_stm >= 0.43: return "the position is roughly equal"
    if win_stm >= 0.3: return f"{other} is slightly better"
    if win_stm >= 0.1: return f"{other} is clearly better"
    return f"{other} is winning"


def _events(text: str, acts: Optional[UiActions] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for sentence in re.findall(r"[^.!?]+[.!?]*\s*", text):
        out.append({"type": "text", "delta": sentence})
    if acts and not acts.is_empty():
        out.append({"type": "ui", **acts.to_dict()})
    return out


def _user_move(board: chess.Board, message: str) -> Optional[chess.Move]:
    for tok in re.findall(r"[A-Za-z0-9=+#\-]{2,8}", message):
        mv = parse_move(board, tok.rstrip("+#"))
        if mv:
            return mv
    return None


async def offline_coach(mode: str, board: chess.Board, message: str, history_len: int, ex: ToolExecutor,
                        facts: Dict[str, Any], motif_facts: Dict[str, Any],
                        game_ctx: Optional[Dict[str, Any]]) -> AsyncIterator[Dict[str, Any]]:
    stm = chess.COLOR_NAMES[board.turn]
    lines = facts.get("lines", [])
    if facts.get("terminal"):
        yield {"type": "text", "delta": f"The game is over here ({facts['terminal']})."}
        return
    best = lines[0] if lines else None
    win_stm = best["win_prob_side_to_move"] if best else 0.5
    mv_best = chess.Move.from_uci(best["uci"]) if best else None

    if mode == "explain":
        parts = [f"{_standing(win_stm, stm).capitalize()} ({win_stm * 100:.0f}% for {stm}, engine depth {facts['depth']})."]
        acts = UiActions()
        if best:
            parts.append(f"The engine's top choice is {best['move']}, with the main line {' '.join(best['pv'][:5])}.")
            created = mt.move_creates(board, mv_best)
            if created:
                parts.append("It works because " + "; ".join(m.description for m in created[:2]) + ".")
            elif board.is_capture(mv_best):
                parts.append("It wins material.")
            elif board.gives_check(mv_best):
                parts.append("It starts with a check that keeps the initiative.")
            acts.arrows.append({"from": best["uci"][:2], "to": best["uci"][2:4], "color": "green"})
            if len(lines) > 1 and lines[0]["win_prob_side_to_move"] - lines[1]["win_prob_side_to_move"] >= 0.15:
                parts.append(f"It is close to the only move: {lines[1]['move']} gives up "
                             f"{(lines[0]['win_prob_side_to_move'] - lines[1]['win_prob_side_to_move']) * 100:.0f}% win chance.")
        loose = [m for m in motif_facts.get("motifs", []) if m["type"] == "hanging"]
        for m in loose[:2]:
            parts.append(f"Watch out: {m['description']}.")
            acts.highlights.append(m["squares"][0])
        others = [m for m in motif_facts.get("motifs", []) if m["type"] != "hanging"][:2]
        for m in others:
            parts.append(f"Pattern on the board: {m['description']}.")
        if game_ctx and game_ctx.get("last_move"):
            lm = game_ctx["last_move"]
            parts.append(f"The move just played, {lm['san']}, was {LABEL_TEXT.get(lm['label'], lm['label'])}.")
        for e in _events(" ".join(parts), acts):
            yield e
        return

    if mode == "socratic_hint":
        user_mv = _user_move(board, message) if message else None
        if user_mv:
            r, _ = await ex.run("test_candidate_move", {"fen": board.fen(), "move": board.san(user_mv)})
            if r.get("is_engine_best"):
                txt = f"Yes, {r['move']} is exactly what the engine wants. Nice find."
            else:
                txt = (f"{r['move']} loses about {r['win_prob_loss'] * 100:.0f}% win chance. The engine answers "
                       f"with {r.get('opponent_best_reply')}. Look again: any checks, captures or threats you missed?")
            for e in _events(txt):
                yield e
            return
        level = min(3, history_len)
        if not best:
            return
        piece = PIECE_NAMES[board.piece_type_at(mv_best.from_square)]
        acts = UiActions()
        if level == 0:
            txt = ("Hint 1: before moving, list every check, capture and threat, yours and your opponent's. "
                   "Is any piece loose? Which of your pieces is doing the least?")
            for h in [m for m in motif_facts.get("motifs", []) if m["type"] == "hanging"][:1]:
                acts.highlights.append(h["squares"][0])
        elif level == 1:
            txt = f"Hint 2: the best move is a {piece} move. Think about what it attacks or defends."
        elif level == 2:
            txt = "Hint 3: I've highlighted the area. Which move there changes the position the most?"
            acts.highlights += [best["uci"][:2], best["uci"][2:4]]
        else:
            txt = f"The best move is {best['move']}: " + (" ".join(best["pv"][:4])) + "."
            acts.arrows.append({"from": best["uci"][:2], "to": best["uci"][2:4], "color": "green"})
        for e in _events(txt, acts):
            yield e
        return

    # quiz_me
    user_mv = _user_move(board, message) if message else None
    if user_mv:
        r, _ = await ex.run("test_candidate_move", {"fen": board.fen(), "move": board.san(user_mv)})
        acts = UiActions()
        if r.get("is_engine_best"):
            txt = f"Correct! {r['move']} is the engine's first choice."
        elif r.get("win_prob_loss", 1) <= 0.05:
            txt = (f"{r['move']} is playable (it only costs {r['win_prob_loss'] * 100:.0f}% win chance), "
                   f"but the best move was {r['best_move']}.")
            acts.arrows.append({"from": mv_best.uci()[:2], "to": mv_best.uci()[2:4], "color": "green"})
        else:
            txt = (f"Not quite. {r['move']} loses about {r['win_prob_loss'] * 100:.0f}% win chance: "
                   f"the engine replies {r.get('opponent_best_reply')}. The best move was {r['best_move']}.")
            acts.arrows.append({"from": mv_best.uci()[:2], "to": mv_best.uci()[2:4], "color": "green"})
        txt += " Want another position? Use the quiz button on a different move."
        for e in _events(txt, acts):
            yield e
        return
    txt = (f"Quiz time! It's {stm}'s move. Find the best move and reply with it (like Nf3 or e4). "
           f"Hint: the engine rates this position {_standing(win_stm, stm).lower()}.")
    for e in _events(txt):
        yield e
