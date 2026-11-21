from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from sqlalchemy import select

from ..coach.agent import run_coach
from ..models.db import Game, MoveRow, Review
from ..models.schemas import CoachRequest
from .deps import session
from .sse import sse, sse_response

log = logging.getLogger("chessy.coach")
router = APIRouter(prefix="/api/coach", tags=["coach"])


async def _game_context(request: Request, req: CoachRequest) -> Optional[Dict[str, Any]]:
    if not req.game_id:
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


@router.post("/chat")
async def chat(body: CoachRequest, request: Request):
    st = request.app.state
    ctx = await _game_context(request, body)

    async def gen():
        try:
            async for ev in run_coach(body, st.executor, st.adapter, st.settings, ctx):
                yield sse(ev["type"], ev)
        except Exception as e:
            log.exception("coach failed")
            yield sse("error", {"type": "error", "message": f"Coach error: {e}"})
            yield sse("done", {"type": "done"})

    return sse_response(gen())
