from fastapi import APIRouter, HTTPException, Request

from ..services.analytics import player_insights
from .deps import session

router = APIRouter(prefix="/api/players", tags=["players"])


@router.get("/{username}/insights")
async def insights(username: str, request: Request):
    async with session(request) as s:
        res = await player_insights(s, username)
    if res is None:
        raise HTTPException(404, f"No imported games found for '{username}'. Import some games first.")
    return res
