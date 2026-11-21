from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


def state(request: Request):
    return request.app.state


def session(request: Request) -> AsyncSession:
    return request.app.state.sessionmaker()
