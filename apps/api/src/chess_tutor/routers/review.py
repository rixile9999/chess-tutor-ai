"""Review endpoints (layers 2-4). Thin: assembly lives in services.review, prose in
services.verbalize. A game that has not been analysed yet is analysed inline first."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import models
from chess_tutor.config import get_settings
from chess_tutor.db import get_session
from chess_tutor.schemas import Classification, Color, GameAnalysis, MoveReviewOut
from chess_tutor.services import analysis as analysis_svc
from chess_tutor.services import review as review_svc

router = APIRouter(prefix="/review", tags=["review"])

Session = Annotated[AsyncSession, Depends(get_session)]
Depth = Annotated[int | None, Query(ge=1, le=40, description="분석 깊이. 비우면 설정값")]
Rating = Annotated[
    int | None, Query(ge=400, le=3200, description="사람 관점을 맞출 레이팅. 비우면 설정값")
]

NOT_FOUND = "게임을 찾지 못했습니다."
PLY_NOT_FOUND = "해당 수를 찾지 못했습니다."
ANALYSIS_FAILED = "엔진 분석을 완료하지 못했습니다."


class MoveListItem(BaseModel):
    ply: int
    san: str
    color: Color
    classification: Classification


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "review", "status": "ok"}


async def _analysis(game_id: int, depth: int | None) -> GameAnalysis:
    analysis = await analysis_svc.get_or_analyze(game_id, depth=depth)
    if analysis.status != "done":
        detail = ANALYSIS_FAILED if not analysis.error else f"{ANALYSIS_FAILED} {analysis.error}"
        raise HTTPException(status_code=503, detail=detail)
    return analysis


@router.get("/{game_id}", response_model=list[MoveListItem])
async def move_list(game_id: int, session: Session, depth: Depth = None) -> list[MoveListItem]:
    """Classification per move for the move list."""
    if await session.get(models.Game, game_id) is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    analysis = await _analysis(game_id, depth)
    return [MoveListItem.model_validate(item) for item in review_svc.move_list(analysis)]


@router.get("/{game_id}/{ply}", response_model=MoveReviewOut)
async def move_review(
    game_id: int, ply: int, session: Session, rating: Rating = None, depth: Depth = None
) -> MoveReviewOut:
    """Full review of one move (1-based ply): refutation, alternatives, comparison, human and
    strategy views, arrows and the verified explanation."""
    game = await session.get(models.Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    analysis = await _analysis(game_id, depth)
    if not 1 <= ply <= len(analysis.moves):
        raise HTTPException(status_code=404, detail=PLY_NOT_FOUND)
    return await review_svc.build_move_review(
        session, game, analysis, ply, rating or get_settings().default_rating
    )
