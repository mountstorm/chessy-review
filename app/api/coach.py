from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from ..coach.agent import run_coach
from ..engine.scope import engine_scope
from ..coach.tools import ToolExecutor
from ..models.db import Game, MoveRow, Review
from ..models.schemas import CoachRequest
from ..ratelimit import rate_limit
from .deps import session
from .sse import sse, sse_response

log = logging.getLogger("chessy.coach")
router = APIRouter(prefix="/api/coach", tags=["coach"])


async def _game_context(request: Request, req: CoachRequest) -> Optional[Dict[str, Any]]:
    if req.context:                                   # client-supplied (hosted mode has no database)
        c = req.context
        return {"white": c.white, "black": c.black, "opening": c.opening,
                **({"last_move": {k: str(v)[:300] for k, v in c.last_move.items() if k in ("san", "label", "comment")}}
                   if c.last_move and {"san", "label", "comment"} <= set(c.last_move) else {})}
    if not req.game_id or request.app.state.sessionmaker is None:
        return None
    async with session(request) as s:
        g = await s.get(Game, req.game_id)
        if not g:
            return None
        ctx: Dict[str, Any] = {"white": g.white, "black": g.black, "opening": g.opening}
        ply = len(req.move_history)
        if ply:
            reviews = (await s.execute(select(Review).where(Review.game_id == g.id, Review.complete.is_(True)))).scalars().all()
            if reviews:
                r = max(reviews, key=lambda r: (r.depth, r.multipv))
                row = (await s.execute(select(MoveRow).where(MoveRow.review_id == r.id, MoveRow.ply == ply))).scalar_one_or_none()
                if row:
                    ctx["last_move"] = {"san": row.data["san"], "label": row.label, "comment": row.data["comment"]}
        return ctx


@router.post("/chat", dependencies=[Depends(rate_limit("coach"))])
async def chat(body: CoachRequest, request: Request):
    st = request.app.state
    ctx = await _game_context(request, body)

    async def gen():
        try:
            async with engine_scope(request.app) as pool:
                async for ev in run_coach(body, ToolExecutor(pool), st.adapter, st.settings, ctx):
                    yield sse(ev["type"], ev)
        except Exception as e:
            log.exception("coach failed")
            yield sse("error", {"type": "error", "message": f"Coach error: {e}"})
            yield sse("done", {"type": "done"})

    return sse_response(gen())
