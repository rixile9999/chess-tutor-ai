"""Opening visualisations: overlay DAG, piece destination heatmap, pawn break timing.

Games are the user's own (models.User join) played with the requested colour. All the chess
work happens in chess_tutor.services.openings_map and runs in a worker thread so PGN parsing
never blocks the event loop.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.db import get_session
from chess_tutor.models import Game, User
from chess_tutor.schemas import BreakTiming, Color, OpeningMap, PieceHeatmap
from chess_tutor.services.openings_map import (
    COLOR_NAMES_KO,
    break_timing,
    build_map,
    piece_heatmap,
)

router = APIRouter(prefix="/openings", tags=["openings"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "openings", "status": "ready"}


async def _user_games(
    session: AsyncSession, username: str, color: Color, platform: str | None
) -> list[Game]:
    stmt = (
        select(Game)
        .join(User, Game.user_id == User.id)
        .where(User.username == username, Game.user_color == color)
        .order_by(Game.played_at, Game.id)
    )
    if platform:
        stmt = stmt.where(User.platform == platform)
    games = list((await session.execute(stmt)).scalars().all())
    if not games:
        raise HTTPException(
            status_code=404,
            detail=f"{username}의 {COLOR_NAMES_KO[color]} 기보가 없습니다. 먼저 기보를 가져오세요.",
        )
    return games


@router.get("/map", response_model=OpeningMap)
async def opening_map(
    session: SessionDep,
    username: str,
    color: Color,
    depth: Annotated[int, Query(ge=1, le=40, description="플라이 단위 깊이")] = 12,
    min_games: Annotated[int, Query(ge=1)] = 2,
    platform: str | None = None,
) -> OpeningMap:
    games = await _user_games(session, username, color, platform)
    return await asyncio.to_thread(build_map, games, color, depth, min_games)


@router.get("/heatmap", response_model=PieceHeatmap)
async def heatmap(
    session: SessionDep,
    username: str,
    color: Color,
    piece: Annotated[str, Query(min_length=3, max_length=3, description="예: bf8")],
    through_move: Annotated[int, Query(ge=1, le=60)] = 15,
    platform: str | None = None,
) -> PieceHeatmap:
    games = await _user_games(session, username, color, platform)
    try:
        return await asyncio.to_thread(piece_heatmap, games, color, piece, through_move)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/breaks", response_model=list[BreakTiming])
async def breaks(
    session: SessionDep,
    username: str,
    color: Color,
    structure: str | None = None,
    platform: str | None = None,
) -> list[BreakTiming]:
    games = await _user_games(session, username, color, platform)
    return await asyncio.to_thread(break_timing, games, color, structure)
