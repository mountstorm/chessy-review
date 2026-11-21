"""Cross-game habit analytics from stored reviews."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..coach.resources import THEMES
from ..models.db import Game, MoveRow, Review

LOW_CLOCK_SECONDS = 30.0
ERROR_LABELS = {"inaccuracy", "mistake", "blunder", "missed_win"}


def _avg(xs: List[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 1) if xs else None


async def player_insights(session: AsyncSession, username: str) -> Optional[Dict[str, Any]]:
    u = username.lower()
    games = (await session.execute(
        select(Game).where(or_(func.lower(Game.white) == u, func.lower(Game.black) == u)))).scalars().all()
    if not games:
        return None
    by_id = {g.id: g for g in games}

    # best (deepest) complete review per game
    reviews = (await session.execute(
        select(Review).where(Review.game_id.in_(by_id), Review.complete.is_(True)))).scalars().all()
    best: Dict[str, Review] = {}
    for r in reviews:
        cur = best.get(r.game_id)
        if cur is None or (r.depth, r.multipv) > (cur.depth, cur.multipv):
            best[r.game_id] = r
    if not best:
        return {"username": username, "games_found": len(games), "games_analyzed": 0,
                "note": "Games imported, but none have been analyzed yet. Run /api/games/{id}/analyze first."}

    review_game = {r.id: r.game_id for r in best.values()}
    rows = (await session.execute(select(MoveRow).where(MoveRow.review_id.in_(review_game)))).scalars().all()

    phase_acc: Dict[str, List[float]] = defaultdict(list)
    labels = Counter()
    missed = Counter()
    low = {"moves": 0, "blunders": 0}
    normal = {"moves": 0, "blunders": 0}
    opening_acc: Dict[str, List[float]] = defaultdict(list)
    opening_games: Dict[str, set] = defaultdict(set)
    game_acc: Dict[str, List[float]] = defaultdict(list)

    for row in rows:
        g = by_id[review_game[row.review_id]]
        mine_color = "white" if g.white.lower() == u else "black"
        if row.mover != mine_color:
            continue
        phase_acc[row.phase].append(row.accuracy)
        labels[row.label] += 1
        game_acc[g.id].append(row.accuracy)
        if row.label in ERROR_LABELS:
            for m in row.missed_motifs or []:
                missed[m] += 1
        if row.clock_left is not None:
            bucket = low if row.clock_left < LOW_CLOCK_SECONDS else normal
            bucket["moves"] += 1
            bucket["blunders"] += row.label == "blunder"
        name = g.opening or g.eco or "Unknown opening"
        opening_acc[name].append(row.accuracy)
        opening_games[name].add(g.id)

    def rate(b):
        return round(b["blunders"] / b["moves"], 3) if b["moves"] else None

    openings = sorted(
        ({"opening": k, "games": len(opening_games[k]), "avg_accuracy": _avg(v)} for k, v in opening_acc.items()),
        key=lambda x: x["avg_accuracy"] if x["avg_accuracy"] is not None else 101)[:5]

    phases = {p: _avg(v) for p, v in phase_acc.items()}
    weakest_phase = min(phases, key=lambda p: phases[p]) if phases else None
    total = sum(labels.values()) or 1
    advice = []
    for motif, n in missed.most_common(2):
        theme = {"hanging": "hanging", "mate": "mate_missed", "fork": "missed_tactic", "pin": "missed_tactic",
                 "skewer": "missed_tactic", "discovered_attack": "missed_tactic"}.get(motif)
        if theme:
            advice.append({"reason": f"You missed '{motif.replace('_', ' ')}' ideas {n} times",
                           "title": THEMES[theme]["title"], "habit": THEMES[theme]["habit"],
                           "links": [{"label": a, "url": b} for a, b in THEMES[theme]["links"]]})
    if weakest_phase in ("opening", "endgame"):
        t = THEMES[weakest_phase]
        advice.append({"reason": f"Your weakest phase is the {weakest_phase} ({phases[weakest_phase]}% accuracy)",
                       "title": t["title"], "habit": t["habit"], "links": [{"label": a, "url": b} for a, b in t["links"]]})
    lr, nr = rate(low), rate(normal)
    return {
        "username": username, "games_found": len(games), "games_analyzed": len(best),
        "moves_analyzed": sum(labels.values()),
        "overall_accuracy": _avg([a for v in game_acc.values() for a in v]),
        "accuracy_by_phase": phases, "weakest_phase": weakest_phase,
        "label_distribution": {k: round(v / total, 3) for k, v in labels.items()},
        "blunder_rate": {"low_clock": lr, "normal_clock": nr, "low_clock_moves": low["moves"],
                         "threshold_seconds": LOW_CLOCK_SECONDS,
                         "verdict": ("You blunder more when short on time." if lr is not None and nr is not None and lr > nr * 1.5
                                     else "Clock pressure isn't your main issue." if lr is not None else "No clock data in these games.")},
        "most_missed_motifs": [{"motif": m, "count": n} for m, n in missed.most_common(6)],
        "weakest_openings": openings,
        "advice": advice,
    }
