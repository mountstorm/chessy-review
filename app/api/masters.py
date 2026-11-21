from fastapi import APIRouter

from ..models.schemas import MastersRequest
from ..services import masters

router = APIRouter(prefix="/api/masters", tags=["masters"])


@router.post("")
async def lookup(body: MastersRequest):
    """Stateless: where does this move sequence leave master theory, and which master games are closest?"""
    return await masters.walk_book(body.sans)
