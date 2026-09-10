"""Position guide for the opening map (M8a): book candidates, structure and setup progress.

Stateless — nothing here reads the database; the user's own record is overlaid in the browser
from the map it already fetched (`GET /openings/map`). Shares the /openings prefix with
routers/openings.py; the two are merged in M8d.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from chess_tutor.schemas import Color, PositionGuide
from chess_tutor.services import opening_guide

router = APIRouter(prefix="/openings", tags=["openings"])


@router.get("/position", response_model=PositionGuide)
async def position(
    fen: Annotated[str, Query(min_length=10, description="국면 FEN")],
    color: Annotated[Color, Query(description="내 색. 마스터 승률을 이 쪽에서 봅니다")] = "white",
    masters: Annotated[bool, Query(description="마스터 통계를 겹칠지(토큰이 있을 때만)")] = False,
) -> PositionGuide:
    """Next named positions, the pawn structure and every setup's progress.

    The book work is a few milliseconds; only the master overlay touches the network, and it
    runs in a worker thread so its three-second timeout never blocks the event loop."""
    try:
        return await asyncio.to_thread(opening_guide.guide, fen, color, masters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
