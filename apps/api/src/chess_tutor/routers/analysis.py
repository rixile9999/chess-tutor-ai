"""Engine analysis endpoints (layer 1). Thin: the pipeline lives in services.analysis."""

from __future__ import annotations

import asyncio
from typing import Annotated

import chess
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import models
from chess_tutor.config import get_settings
from chess_tutor.db import get_session
from chess_tutor.jobs import runner
from chess_tutor.schemas import EngineLine, GameAnalysis
from chess_tutor.services import analysis as svc

router = APIRouter(prefix="/analysis", tags=["analysis"])

Session = Annotated[AsyncSession, Depends(get_session)]
Depth = Annotated[int | None, Query(ge=1, le=40, description="탐색 깊이. 비우면 설정값")]
MultiPV = Annotated[int | None, Query(ge=1, le=10, description="후보 수의 개수. 비우면 설정값")]

NOT_FOUND = "게임을 찾지 못했습니다."
NO_ENGINE = "엔진을 찾을 수 없습니다. STOCKFISH_PATH를 확인해 주세요."


class PositionAnalysisRequest(BaseModel):
    fen: str
    depth: int | None = Field(default=None, ge=1, le=40)
    multipv: int | None = Field(default=None, ge=1, le=10)


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "analysis", "status": "ok"}


@router.post("/position", response_model=list[EngineLine])
async def analyse_position(req: PositionAnalysisRequest) -> list[EngineLine]:
    """Ad-hoc engine lines for one position, cached by FEN, engine, depth and multipv."""
    try:
        chess.Board(req.fen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FEN을 읽을 수 없습니다: {exc}") from exc
    try:
        return await svc.get_lines(req.fen, depth=req.depth, multipv=req.multipv)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=NO_ENGINE) from exc


@router.post("/{game_id}", response_model=GameAnalysis)
async def start_analysis(
    game_id: int, session: Session, depth: Depth = None, multipv: MultiPV = None
) -> GameAnalysis:
    """Queue a full-game analysis. A run already in progress is returned as is; a finished or
    failed one is reset and started again."""
    game = await session.get(models.Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    job = runner.get(svc.job_key(game_id))
    if job is not None and job.status in ("pending", "running"):
        return await svc.get_analysis(game_id)
    try:
        engine = await asyncio.to_thread(svc.engine_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=NO_ENGINE) from exc
    row = await svc.reset_analysis(
        session, game_id, depth or get_settings().engine_depth, engine, status="pending"
    )
    await session.commit()
    result = svc.to_game_analysis(row)
    svc.submit_analysis(game_id, depth, multipv)
    return result


@router.get("/{game_id}", response_model=GameAnalysis)
async def get_analysis(game_id: int, session: Session) -> GameAnalysis:
    """Current state; status 'none' when the game has never been analysed."""
    if await session.get(models.Game, game_id) is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    row = await svc.get_analysis_row(session, game_id)
    if row is None:
        return GameAnalysis(game_id=game_id, status="none")
    return svc.to_game_analysis(row)
