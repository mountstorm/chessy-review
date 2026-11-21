from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession


def state(request: Request):
    return request.app.state


def session(request: Request) -> AsyncSession:
    sm = request.app.state.sessionmaker
    if sm is None:
        raise HTTPException(501, "Not available in hosted mode: games aren't stored on the server. "
                                 "Run Chessy locally for saved games and insights.")
    return sm()
