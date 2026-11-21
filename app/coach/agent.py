"""Coach agent loop. Grounding is enforced two ways: engine facts for the current position are computed
server-side and injected before the model speaks, and the model's tool calls run against the real engine."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncIterator, Dict, List, Optional

import chess

from ..config import Settings
from ..models.schemas import CoachRequest
from .actions import TagStream
from .llm import LLMAdapter, LLMError, StepEnd, TextDelta
from .offline import offline_coach
from .prompts import LOCAL_REWRITE_SYSTEM, system_prompt
from .tools import EvaluateArgs, ToolExecutor

log = logging.getLogger("chessy.coach")


def _history_text(moves: List[str]) -> str:
    out = []
    for i, m in enumerate(moves):
        out.append(f"{i // 2 + 1}. {m}" if i % 2 == 0 else m)
    return " ".join(out) if out else "(none given)"


def _facts_text(facts: Dict[str, Any], motifs: Dict[str, Any]) -> str:
    lines = [f"  {l['rank']}. {l['move']}  eval(white)={l['cp_white']}cp"
             + (f" mate={l['mate']}" if l["mate"] is not None else "")
             + f"  win%={l['win_prob_side_to_move'] * 100:.0f} for side to move  line: {' '.join(l['pv'][:6])}"
             for l in facts.get("lines", [])]
    ms = [m["description"] for m in motifs.get("motifs", [])] or ["none detected"]
    return ("ENGINE FACTS (Stockfish depth %s):\n  side to move: %s%s\n%s\nTACTICAL MOTIFS: %s"
            % (facts.get("depth"), facts.get("side_to_move"), " (in check)" if facts.get("in_check") else "",
               "\n".join(lines), "; ".join(ms)))


def build_user_message(req: CoachRequest, facts: Dict[str, Any], motifs: Dict[str, Any],
                       game_ctx: Optional[Dict[str, Any]]) -> str:
    parts = [f"CURRENT FEN: {req.current_fen}", f"MOVES SO FAR: {_history_text(req.move_history)}"]
    if game_ctx:
        parts.append(f"GAME: {game_ctx.get('white')} vs {game_ctx.get('black')}"
                     + (f", {game_ctx['opening']}" if game_ctx.get("opening") else ""))
        lm = game_ctx.get("last_move")
        if lm:
            parts.append(f"REVIEW NOTE for the last move {lm['san']}: classified {lm['label']}; {lm['comment']}")
    parts.append(_facts_text(facts, motifs))
    msg = req.user_message.strip() or {
        "explain": "Explain this position to me.",
        "socratic_hint": "Give me a hint for this position.",
        "quiz_me": "Quiz me on this position.",
    }[req.mode]
    parts.append(f"USER: {msg}")
    return "\n\n".join(parts)


# piece moves, pawn captures and castling only: bare squares ("on e5") are ordinary prose, not claims about moves
_SAN = re.compile(r"(?<![\w-])(?:O-O-O|O-O|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8](?:=[QRBN])?)[+#]?(?![\w])")


def unverified_moves(text: str, board: chess.Board, facts: Dict[str, Any], history: List[str],
                     extra: Optional[List[str]] = None) -> List[str]:
    """Moves a small model mentioned that are neither legal here, nor in the engine lines, nor in the game so far.
    A gentle guard against a weak model inventing chess; it flags, it never rewrites."""
    known = set(history) | set(extra or [])
    for l in facts.get("lines", []):
        known.update(m.rstrip("+#") for m in l["pv"])
    bad: List[str] = []
    for tok in dict.fromkeys(m.group(0) for m in _SAN.finditer(text)):
        core = tok.rstrip("+#")
        if core in known or any(k.rstrip("+#") == core for k in known):
            continue
        try:
            board.parse_san(core)
        except ValueError:
            bad.append(tok)
    return bad


def _brief(result: Dict[str, Any]) -> str:
    if "error" in result:
        return result["error"]
    if "lines" in result and result["lines"]:
        l = result["lines"][0]
        return f"best {l['move']} ({l['cp_white']}cp)"
    if "classification" in result:
        return f"{result['move']}: {result['classification']}"
    if "count" in result:
        return f"{result['count']} found"
    return "ok"


async def local_coach(req: CoachRequest, board: chess.Board, adapter: LLMAdapter, offline,
                      facts: Dict[str, Any], game_ctx: Optional[Dict[str, Any]]) -> AsyncIterator[Dict[str, Any]]:
    """Small local models are unreliable at chess, so they never get to invent any. The deterministic coach writes the
    facts; the local model only rephrases them and answers the player's question from them. If the rewrite mentions any
    move that is not in the facts, the deterministic text replaces it. Hints and quizzes stay fully deterministic
    (a model could leak the answer)."""
    if req.mode != "explain":
        async for ev in offline():
            yield ev
        return
    det = [e async for e in offline()]
    det_text = "".join(e["delta"] for e in det if e["type"] == "text")
    for e in det:
        if e["type"] == "ui":
            yield e
    note = ""
    if game_ctx and game_ctx.get("last_move"):
        note = f"\nReview note: {game_ctx['last_move']['san']} was classified {game_ctx['last_move']['label']}."
    question = req.user_message.strip() or "Explain this position to me."
    prompt = (f"FACTS (from Stockfish):\n{det_text}{note}\n\nSide to move: {facts['side_to_move']}.\n"
              f"QUESTION: {question}")
    messages = [{"role": t.role, "content": t.content} for t in req.history[-6:]] + [{"role": "user", "content": prompt}]
    state = adapter.new_state(LOCAL_REWRITE_SYSTEM, messages)
    state["no_tools"], state["temperature"] = True, 0.3
    tags, spoken = TagStream(), []
    try:
        async for ev in adapter.step(state):
            if isinstance(ev, TextDelta):
                text, _ = tags.feed(ev.text)
                if text:
                    spoken.append(text)
                    yield {"type": "text", "delta": text}
    except LLMError as e:
        yield {"type": "notice", "message": f"{e} Using the engine's own explanation instead."}
        yield {"type": "text", "delta": det_text}
        return
    tail, _ = tags.flush()
    full = "".join(spoken) + tail
    if tail:
        yield {"type": "text", "delta": tail}
    known = [m.group(0) for m in _SAN.finditer(det_text)]
    bad = unverified_moves(full, board, facts, req.move_history, known)
    if bad or len(full.strip()) < 20:
        why = f"it mentioned {', '.join(bad[:4])}, which the engine didn't give" if bad else "it gave no usable answer"
        yield {"type": "replace", "text": det_text}
        yield {"type": "notice", "message": f"The local model's wording was discarded ({why}). Showing the engine's own explanation."}


async def run_coach(req: CoachRequest, executor: ToolExecutor, adapter: Optional[LLMAdapter],
                    settings: Settings, game_ctx: Optional[Dict[str, Any]] = None) -> AsyncIterator[Dict[str, Any]]:
    board = chess.Board(req.current_fen)
    (facts, f_err), (motifs, _) = await asyncio.gather(
        executor.run("evaluate_fen", {"fen": req.current_fen, "depth": 14, "multipv": 3}),
        executor.run("get_tactical_motifs", {"fen": req.current_fen}))
    if f_err:
        yield {"type": "error", "message": facts.get("error", "engine error")}
        return
    best = facts["lines"][0] if facts.get("lines") else None
    yield {"type": "grounding", "side_to_move": facts["side_to_move"], "eval_cp_white": facts["eval_cp_white"],
           "win_prob_white": facts["win_prob_white"], "best": best["move"] if best else None,
           "best_uci": best["uci"] if best else None, "mode": req.mode,
           "provider": adapter.provider if adapter else "offline"}

    async def offline() -> AsyncIterator[Dict[str, Any]]:
        hist = sum(1 for t in req.history if t.role == "assistant")
        async for ev in offline_coach(req.mode, board, req.user_message, hist, executor, facts, motifs, game_ctx):
            yield ev

    if adapter is None:
        async for ev in offline():
            yield ev
        yield {"type": "done"}
        return

    if adapter.provider == "local":
        async for ev in local_coach(req, board, adapter, offline, facts, game_ctx):
            yield ev
        yield {"type": "done"}
        return

    messages = [{"role": t.role, "content": t.content} for t in req.history]
    messages.append({"role": "user", "content": build_user_message(req, facts, motifs, game_ctx)})
    state = adapter.new_state(system_prompt(req.mode, req.player_rating), messages)
    tags = TagStream()
    spoken: List[str] = []
    try:
        for _ in range(settings.coach_max_tool_rounds):
            end: Optional[StepEnd] = None
            async for ev in adapter.step(state):
                if isinstance(ev, TextDelta):
                    text, acts = tags.feed(ev.text)
                    if text:
                        spoken.append(text)
                        yield {"type": "text", "delta": text}
                    if not acts.is_empty():
                        yield {"type": "ui", **acts.to_dict()}
                else:
                    end = ev
            if end is None or end.refused:
                yield {"type": "error", "message": "The coach declined to answer that request."}
                break
            if not end.tool_calls:
                break
            for c in end.tool_calls:
                yield {"type": "tool", "name": c.name, "input": c.input}
            results = await asyncio.gather(*(executor.run(c.name, c.input) for c in end.tool_calls))
            packed = [(c, r, err) for c, (r, err) in zip(end.tool_calls, results)]
            for c, r, err in packed:
                yield {"type": "tool_result", "name": c.name, "ok": not err, "summary": _brief(r)}
            adapter.add_tool_results(state, packed)
        else:
            yield {"type": "error", "message": "The coach used too many tool rounds without finishing."}
        tail, _ = tags.flush()
        if tail:
            spoken.append(tail)
            yield {"type": "text", "delta": tail}
    except LLMError as e:
        log.warning("LLM failed, falling back to offline coach: %s", e)
        yield {"type": "notice", "message": f"{e} Using the offline engine coach instead."}
        async for ev in offline():
            yield ev
    yield {"type": "done"}
