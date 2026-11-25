from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select

from ..coach.offline import with_moments
from ..engine.review import review_game
from ..engine.scope import engine_scope
from ..models.db import Game, MoveRow, Review
from ..models.schemas import AnalyzeRequest, GameSummary, ImportRequest
from ..ratelimit import rate_limit
from ..services import importers, masters, pgn as pgnlib
from ..services.example import EXAMPLE_PGN, load_example
from .deps import session, state
from .sse import sse, sse_response

log = logging.getLogger("chessy.games")
router = APIRouter(prefix="/api/games", tags=["games"])


def _summary(g: Game, analyzed: bool, created: bool = True) -> GameSummary:
    tree = g.tree
    return GameSummary(id=g.id, source=g.source, white=g.white, black=g.black, white_elo=g.white_elo,
                       black_elo=g.black_elo, result=g.result, opening=g.opening, eco=g.eco,
                       time_control=g.time_control or {}, date=(g.meta or {}).get("date"), plies=len(pgnlib.mainline_nodes(tree)),
                       variations=pgnlib.count_variations(tree), analyzed=analyzed, created=created)


async def _analyzed_ids(s, ids: List[str]) -> set:
    if not ids:
        return set()
    rows = await s.execute(select(Review.game_id).where(Review.game_id.in_(ids), Review.complete.is_(True)))
    return {r[0] for r in rows}


@router.post("/import")
async def import_games(body: ImportRequest, request: Request) -> Dict[str, Any]:
    if body.pgn:
        source, text = "pgn", body.pgn
    elif body.chesscom_username:
        source = "chesscom"
        try:
            text = await importers.fetch_chesscom(body.chesscom_username, body.max_games)
        except importers.ImportFailed as e:
            raise HTTPException(e.status, str(e))
    else:
        source = "lichess"
        try:
            text = await importers.fetch_lichess(body.lichess_username, body.max_games)
        except importers.ImportFailed as e:
            raise HTTPException(e.status, str(e))
    try:
        parsed = pgnlib.parse_pgn(text)
    except pgnlib.PGNError as e:
        raise HTTPException(422, str(e))
    parsed = parsed[: (200 if source == "pgn" else body.max_games)]

    if request.app.state.sessionmaker is None:          # hosted: nothing is stored, the browser keeps the games
        out_h = []
        for pg in parsed:
            m = pg.meta
            gid = "h" + hashlib.sha1((source + pg.source_id).encode()).hexdigest()[:10]
            out_h.append({"id": gid, "source": source, "white": m["white"], "black": m["black"], "white_elo": m["white_elo"],
                          "black_elo": m["black_elo"], "result": m["result"], "opening": m["opening"], "eco": m["eco"],
                          "time_control": m["time_control"], "date": m["date"], "plies": len(pgnlib.mainline_nodes(pg.tree)),
                          "variations": pgnlib.count_variations(pg.tree), "analyzed": False, "created": True,
                          "meta": m, "pgn": pg.pgn, "tree": pg.tree, "reviews": []})
        return {"games": out_h, "hosted": True}

    out: List[GameSummary] = []
    async with session(request) as s:
        for pg in parsed:
            existing = (await s.execute(select(Game).where(Game.source == source, Game.source_id == pg.source_id))).scalar_one_or_none()
            if existing:
                out.append(_summary(existing, existing.id in await _analyzed_ids(s, [existing.id]), created=False))
                continue
            m = pg.meta
            g = Game(source=source, source_id=pg.source_id, white=m["white"], black=m["black"], white_elo=m["white_elo"],
                     black_elo=m["black_elo"], result=m["result"], eco=m["eco"], opening=m["opening"],
                     time_control=m["time_control"], meta=m, pgn=pg.pgn, tree=pg.tree)
            s.add(g)
            await s.flush()
            out.append(_summary(g, False))
        await s.commit()
    return {"games": [g.model_dump() for g in out]}


@router.post("/example")
async def import_example(request: Request) -> Dict[str, Any]:
    """The built-in example game plus its precomputed review (no engine search). Same response as /import, plus
    `example`: the review's events for the client to replay. With a database the review is saved too, so the coach,
    the library and the insights treat it like any analyzed game."""
    ex = load_example()
    res = await import_games(ImportRequest(pgn=EXAMPLE_PGN), request)
    if request.app.state.sessionmaker is not None:
        gid = res["games"][0]["id"]
        async with session(request) as s:
            done = (await s.execute(select(Review).where(Review.game_id == gid, Review.depth == ex["depth"],
                                                         Review.multipv == ex["multipv"]))).scalar_one_or_none()
            if not (done and done.complete):
                if done:
                    await s.execute(delete(MoveRow).where(MoveRow.review_id == done.id))
                    await s.delete(done)
                    await s.flush()
                review = Review(game_id=gid, depth=ex["depth"], multipv=ex["multipv"], complete=True, summary=ex["summary"])
                s.add(review)
                await s.flush()
                s.add_all(_move_row(review.id, m) for m in ex["moves"])
                await s.commit()
        res["games"][0]["analyzed"] = True
    return {**res, "example": {k: ex[k] for k in ("depth", "multipv", "start", "moves", "summary")}}


@router.get("")
async def list_games(request: Request, limit: int = 50) -> Dict[str, Any]:
    async with session(request) as s:
        games = (await s.execute(select(Game).order_by(Game.created_at.desc()).limit(min(limit, 200)))).scalars().all()
        done = await _analyzed_ids(s, [g.id for g in games])
    return {"games": [_summary(g, g.id in done).model_dump() for g in games]}


async def _get_game(s, game_id: str) -> Game:
    g = await s.get(Game, game_id)
    if not g:
        raise HTTPException(404, "Game not found")
    return g


@router.get("/{game_id}")
async def get_game(game_id: str, request: Request) -> Dict[str, Any]:
    async with session(request) as s:
        g = await _get_game(s, game_id)
        reviews = (await s.execute(select(Review).where(Review.game_id == game_id))).scalars().all()
        return {**_summary(g, any(r.complete for r in reviews)).model_dump(), "meta": g.meta, "pgn": g.pgn, "tree": g.tree,
                "reviews": [{"id": r.id, "depth": r.depth, "multipv": r.multipv, "complete": r.complete} for r in reviews]}


async def _load_review(s, game_id: str, depth: int | None, multipv: int | None):
    q = select(Review).where(Review.game_id == game_id, Review.complete.is_(True))
    if depth:
        q = q.where(Review.depth == depth)
    if multipv:
        q = q.where(Review.multipv == multipv)
    reviews = (await s.execute(q)).scalars().all()
    if not reviews:
        return None, []
    r = max(reviews, key=lambda r: (r.depth, r.multipv))
    rows = (await s.execute(select(MoveRow).where(MoveRow.review_id == r.id).order_by(MoveRow.ply))).scalars().all()
    return r, rows


@router.get("/{game_id}/review")
async def get_review(game_id: str, request: Request, depth: int | None = None, multipv: int | None = None):
    async with session(request) as s:
        await _get_game(s, game_id)
        r, rows = await _load_review(s, game_id, depth, multipv)
        if not r:
            raise HTTPException(404, "No completed review for this game yet. POST /analyze first.")
        moves = [m.data for m in rows]
        return {"depth": r.depth, "multipv": r.multipv, "summary": with_moments(r.summary, moves), "moves": moves}


def _move_row(review_id: str, d: Dict[str, Any]) -> MoveRow:
    return MoveRow(review_id=review_id, ply=d["ply"], mover=d["mover"], phase=d["phase"], label=d["label"],
                   loss=d["loss"], accuracy=d["accuracy"], clock_left=d.get("clock_left"),
                   missed_motifs=d.get("missed_motifs", []), data=d)


@router.post("/{game_id}/analyze", dependencies=[Depends(rate_limit("analyze"))])
async def analyze(game_id: str, body: AnalyzeRequest, request: Request):
    st = state(request)
    if st.sessionmaker is None:
        return await _hosted_analyze(body, request)
    depth = min(body.depth, st.settings.max_depth)
    async with session(request) as s:
        g = await _get_game(s, game_id)
        start_fen, nodes, meta = g.tree["fen"], pgnlib.mainline_nodes(g.tree), g.meta
    if not nodes:
        raise HTTPException(422, "This game has no moves to analyze.")

    async def gen():
        try:
            async with session(request) as s:
                review = (await s.execute(select(Review).where(Review.game_id == game_id, Review.depth == depth,
                                                               Review.multipv == body.multipv))).scalar_one_or_none()
                game_info = {"id": game_id, "white": meta.get("white"), "black": meta.get("black"),
                             "opening": meta.get("opening"), "result": meta.get("result")}
                if review and review.complete and not body.force:
                    rows = (await s.execute(select(MoveRow).where(MoveRow.review_id == review.id).order_by(MoveRow.ply))).scalars().all()
                    evals = review.summary.get("eval") or []
                    yield sse("start", {"fen": start_fen, "eval": evals[0] if evals else None, "total": len(rows),
                                        "replay": True, "game": game_info, "depth": depth})
                    for i, row in enumerate(rows):
                        yield sse("move", {"move": row.data, "progress": {"done": i + 1, "total": len(rows)}})
                    yield sse("summary", {"summary": with_moments(review.summary, [r.data for r in rows])})
                    yield sse("done", {"review_id": review.id, "cached": True})
                    return
                if review:
                    await s.execute(delete(MoveRow).where(MoveRow.review_id == review.id))
                    await s.delete(review)
                    await s.flush()
                review = Review(game_id=game_id, depth=depth, multipv=body.multipv, complete=False)
                s.add(review)
                await s.commit()
                seen: List[Dict[str, Any]] = []
                async with engine_scope(request.app) as pool:
                    async for ev in review_game(pool, start_fen, nodes, depth, body.multipv):
                        t = ev["type"]
                        if t == "start":
                            yield sse("start", {**ev, "replay": False, "game": game_info, "depth": depth})
                        elif t == "move":
                            seen.append(ev["move"])
                            s.add(_move_row(review.id, ev["move"]))
                            await s.commit()
                            yield sse("move", ev)
                        elif t == "summary":
                            ev = {**ev, "summary": with_moments(ev["summary"], seen)}
                            review.summary = ev["summary"]
                            review.complete = True
                            await s.commit()
                            yield sse("summary", ev)
                yield sse("done", {"review_id": review.id, "cached": False})
        except Exception as e:   # surface to the client instead of silently dropping the stream
            log.exception("analysis failed")
            yield sse("error", {"message": f"Analysis failed: {e}"})

    return sse_response(gen())


async def _hosted_analyze(body: AnalyzeRequest, request: Request):
    """Stateless review for serverless hosting: the client sends the PGN, results stream back and nothing is stored."""
    st = request.app.state
    if not body.pgn:
        raise HTTPException(422, "Send the game's PGN in `pgn` (hosted mode doesn't store games).")
    try:
        pg = pgnlib.parse_pgn(body.pgn)[0]
    except pgnlib.PGNError as e:
        raise HTTPException(422, str(e))
    nodes = pgnlib.mainline_nodes(pg.tree)
    if len(nodes) > st.settings.hosted_max_plies:
        raise HTTPException(422, f"Hosted mode analyzes up to {st.settings.hosted_max_plies} plies per game "
                                 f"(this one has {len(nodes)}). Run Chessy locally for longer games.")
    depth = min(body.depth, st.settings.hosted_max_depth)
    info = {"id": None, "white": pg.meta["white"], "black": pg.meta["black"], "opening": pg.meta["opening"], "result": pg.meta["result"]}

    async def gen():
        try:
            seen: List[Dict[str, Any]] = []
            async with engine_scope(request.app) as pool:
                async for ev in review_game(pool, pg.tree["fen"], nodes, depth, body.multipv):
                    t = ev["type"]
                    if t == "start":
                        yield sse("start", {**ev, "replay": False, "game": info, "depth": depth})
                    else:
                        if t == "move":
                            seen.append(ev["move"])
                        elif t == "summary":
                            ev = {**ev, "summary": with_moments(ev["summary"], seen)}
                        yield sse(t, ev)
            yield sse("done", {"cached": False})
        except Exception as e:
            log.exception("hosted analysis failed")
            yield sse("error", {"message": f"Analysis failed: {e}"})

    return sse_response(gen())


@router.get("/{game_id}/masters")
async def game_masters(game_id: str, request: Request):
    """Where this game leaves master theory, plus the closest master games (Lichess masters database)."""
    async with session(request) as s:
        g = await _get_game(s, game_id)
        if g.meta.get("masters"):
            return g.meta["masters"]
        sans = [n["san"] for n in pgnlib.mainline_nodes(g.tree)]
        res = await masters.walk_book(sans)
        if not res["error"]:
            g.meta = {**g.meta, "masters": res}
            await s.commit()
        return res
