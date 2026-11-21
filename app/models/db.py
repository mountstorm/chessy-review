from __future__ import annotations

import datetime as dt
import uuid
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _id() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (UniqueConstraint("source", "source_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    source: Mapped[str] = mapped_column(String(16))              # pgn | chesscom | lichess
    source_id: Mapped[str] = mapped_column(String(200))
    white: Mapped[str] = mapped_column(String(100), index=True)
    black: Mapped[str] = mapped_column(String(100), index=True)
    white_elo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    black_elo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(String(10), default="*")
    eco: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    opening: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    time_control: Mapped[dict] = mapped_column(JSON, default=dict)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    pgn: Mapped[str] = mapped_column(Text)
    tree: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("game_id", "depth", "multipv"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    depth: Mapped[int] = mapped_column(Integer)
    multipv: Mapped[int] = mapped_column(Integer)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MoveRow(Base):
    """One analysed ply. `data` is the full API payload; the other columns exist for analytics queries."""
    __tablename__ = "moves"
    __table_args__ = (Index("ix_moves_review_ply", "review_id", "ply"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)
    ply: Mapped[int] = mapped_column(Integer)
    mover: Mapped[str] = mapped_column(String(5))
    phase: Mapped[str] = mapped_column(String(12))
    label: Mapped[str] = mapped_column(String(12))
    loss: Mapped[float] = mapped_column(Float, default=0.0)
    accuracy: Mapped[float] = mapped_column(Float, default=100.0)
    clock_left: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    missed_motifs: Mapped[list] = mapped_column(JSON, default=list)
    data: Mapped[dict] = mapped_column(JSON)


async def make_sessionmaker(url: str):
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
