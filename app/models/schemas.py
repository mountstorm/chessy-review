from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

import chess
from pydantic import BaseModel, Field, field_validator, model_validator


class ImportRequest(BaseModel):
    pgn: Optional[str] = Field(None, max_length=2_000_000, description="Raw PGN text (one or many games)")
    chesscom_username: Optional[str] = None
    lichess_username: Optional[str] = None
    max_games: int = Field(5, ge=1, le=25)

    @model_validator(mode="after")
    def _exactly_one(self):
        given = [x for x in (self.pgn, self.chesscom_username, self.lichess_username) if x]
        if len(given) != 1:
            raise ValueError("Provide exactly one of: pgn, chesscom_username, lichess_username")
        return self


class GameSummary(BaseModel):
    id: str
    source: str
    white: str
    black: str
    white_elo: Optional[int] = None
    black_elo: Optional[int] = None
    result: str
    opening: Optional[str] = None
    eco: Optional[str] = None
    time_control: Dict[str, Any] = {}
    date: Optional[str] = None            # PGN date, e.g. 2026.11.20 (UTC date when the site gives one)
    plies: int
    variations: int = 0
    analyzed: bool = False
    created: bool = True


class AnalyzeRequest(BaseModel):
    depth: int = Field(14, ge=6, le=30)
    multipv: int = Field(3, ge=1, le=5)
    force: bool = False
    pgn: Optional[str] = Field(None, max_length=300_000, description="Hosted mode only: the game to analyze (nothing is stored server-side)")


class MastersRequest(BaseModel):
    sans: List[str] = Field(max_length=60)

    @field_validator("sans")
    @classmethod
    def _sans(cls, v):
        if not v or any(not s or len(s) > 12 for s in v):
            raise ValueError("sans must be a non-empty list of SAN moves")
        return v


class GameContext(BaseModel):
    """Client-supplied game info for the coach when the server has no database (hosted mode)."""
    white: Optional[str] = Field(None, max_length=80)
    black: Optional[str] = Field(None, max_length=80)
    opening: Optional[str] = Field(None, max_length=120)
    last_move: Optional[Dict[str, str]] = None


class EvaluateRequest(BaseModel):
    fen: str = Field(chess.STARTING_FEN)
    moves: List[str] = Field(default_factory=list, description="SAN or UCI moves played from `fen`")
    depth: int = Field(16, ge=6, le=30)
    multipv: int = Field(4, ge=1, le=6)

    @field_validator("fen")
    @classmethod
    def _fen(cls, v):
        try:
            chess.Board(v)
        except ValueError as e:
            raise ValueError(f"Invalid FEN: {e}")
        return v


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class CoachRequest(BaseModel):
    game_id: Optional[str] = None
    current_fen: str
    move_history: List[str] = Field(default_factory=list)
    user_message: str = Field("", max_length=4000)
    mode: Literal["explain", "socratic_hint", "quiz_me"] = "explain"
    history: List[ChatTurn] = Field(default_factory=list, max_length=40)
    player_rating: Optional[int] = Field(None, ge=100, le=3200)
    context: Optional[GameContext] = None

    @field_validator("current_fen")
    @classmethod
    def _fen(cls, v):
        try:
            chess.Board(v)
        except ValueError as e:
            raise ValueError(f"Invalid FEN: {e}")
        return v
