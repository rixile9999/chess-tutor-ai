"""Database tables. Keep analysis payloads in JSON columns; the schema of those payloads is
defined by the pydantic models in schemas.py."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chess_tutor.db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    platform: Mapped[str] = mapped_column(
        String(16), default="chesscom"
    )  # chesscom | lichess | local
    rating_rapid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_blitz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("username", "platform", name="uq_user_platform"),)


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="pgn")  # pgn | chesscom | lichess
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pgn: Mapped[str] = mapped_column(Text)
    white: Mapped[str] = mapped_column(String(64), default="?")
    black: Mapped[str] = mapped_column(String(64), default="?")
    white_elo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    black_elo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(String(8), default="*")
    time_control: Mapped[str | None] = mapped_column(String(32), nullable=True)
    played_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    eco: Mapped[str | None] = mapped_column(String(8), nullable=True)
    opening_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_color: Mapped[str | None] = mapped_column(String(8), nullable=True)  # white | black
    ply_count: Mapped[int] = mapped_column(Integer, default=0)
    headers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    analysis: Mapped[Analysis | None] = relationship(back_populates="game", uselist=False)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_game_source"),
        Index("ix_games_user_played", "user_id", "played_at"),
    )


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), unique=True)
    engine: Mapped[str] = mapped_column(String(64), default="stockfish")
    depth: Mapped[int] = mapped_column(Integer, default=16)
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending|running|done|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # AnalysisSummary
    moves: Mapped[list[Any]] = mapped_column(JSON, default=list)  # list[MoveAnalysis]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    game: Mapped[Game] = relationship(back_populates="analysis")


class MoveReview(Base):
    """Layer 2-4 output for one move of one game (computed lazily, cached here)."""

    __tablename__ = "move_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    ply: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # MoveReviewOut
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("game_id", "ply", name="uq_review_game_ply"),)


class EngineCache(Base):
    __tablename__ = "engine_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    fen: Mapped[str] = mapped_column(String(128))
    engine: Mapped[str] = mapped_column(String(64))
    depth: Mapped[int] = mapped_column(Integer)
    multipv: Mapped[int] = mapped_column(Integer)
    lines: Mapped[list[Any]] = mapped_column(JSON, default=list)  # list[EngineLine]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("fen", "engine", "depth", "multipv", name="uq_engine_cache"),
    )


class Puzzle(Base):
    __tablename__ = "puzzles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"), nullable=True)
    ply: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fen: Mapped[str] = mapped_column(String(128))
    solution: Mapped[list[Any]] = mapped_column(JSON, default=list)  # uci, solver first
    motif: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="own")  # own | lichess
    # spaced repetition (SM-2)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_puzzles_user_due", "user_id", "due_at"),)


class PuzzleAttempt(Base):
    __tablename__ = "puzzle_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    puzzle_id: Mapped[int] = mapped_column(ForeignKey("puzzles.id"))
    correct: Mapped[bool] = mapped_column(Boolean)
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
