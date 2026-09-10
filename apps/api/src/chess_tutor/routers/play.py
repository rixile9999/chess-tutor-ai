"""Practice games (M7): opponent moves, coach hints and checks, saving games, opening catalogue.

Thin: every endpoint delegates to a service module.
  play_opponent    who plays the other side (Maia at a rating, Stockfish at an Elo)
  play_coach       hints (3 levels) and the after-move blunder check
  practice         saving a played game as a `practice` Game row, plan report after analysis
  openings_catalog curated tabiyas built on the bundled lichess opening TSV
Move/hint/check handlers are sync because Maia (torch) and Stockfish block; FastAPI runs them
in its thread pool.
"""

from __future__ import annotations

from typing import Annotated

import chess.engine
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.db import get_session
from chess_tutor.engine import EngineBusy
from chess_tutor.schemas import (
    BookMoves,
    OpeningCard,
    OpeningDetail,
    PlanReport,
    PlayCheckRequest,
    PlayCheckResponse,
    PlayHintRequest,
    PlayHintResponse,
    PlayMoveRequest,
    PlayMoveResponse,
    PracticeGameIn,
    PracticeGameOut,
)
from chess_tutor.services import openings_catalog, play_coach, play_opponent, practice

router = APIRouter(prefix="/play", tags=["play"])

Session = Annotated[AsyncSession, Depends(get_session)]

NO_ENGINE = "엔진을 찾을 수 없습니다. STOCKFISH_PATH를 확인해 주세요."
ENGINE_DIED = "엔진이 분석 도중 종료됐습니다. 국면을 확인한 뒤 다시 시도해 주세요."


def _engine_trouble(exc: RuntimeError) -> HTTPException:
    """503 for anything the engine itself could not do, the way routers/analysis.py answers:
    a killed process, a pool with nothing free, or no binary at all. Every one of them is a
    RuntimeError, so the order of the checks is what tells them apart."""
    if isinstance(exc, chess.engine.EngineTerminatedError):
        return HTTPException(status_code=503, detail=ENGINE_DIED)
    if isinstance(exc, EngineBusy):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=503, detail=NO_ENGINE)


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "play", "status": "ok"}


@router.post("/move", response_model=PlayMoveResponse)
def move(req: PlayMoveRequest) -> PlayMoveResponse:
    """The opponent's reply in this position. Never returns an evaluation."""
    try:
        return play_opponent.choose(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/hint", response_model=PlayHintResponse)
def hint(req: PlayHintRequest) -> PlayHintResponse:
    """Level 1 structure and plans (no move), 2 natural candidates, 3 best move with why."""
    try:
        return play_coach.hint(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _engine_trouble(exc) from exc


@router.post("/check", response_model=PlayCheckResponse)
def check(req: PlayCheckRequest) -> PlayCheckResponse:
    """Classify the move just played (shallow, cached) so the UI can raise a blunder alert."""
    try:
        return play_coach.check(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _engine_trouble(exc) from exc


@router.post("/games", response_model=PracticeGameOut, status_code=201)
async def save_game(req: PracticeGameIn, session: Session) -> PracticeGameOut:
    """Store a played game as source=practice and queue its analysis."""
    try:
        return await practice.save(session, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/report/{game_id}", response_model=PlanReport)
async def report(game_id: int, session: Session) -> PlanReport:
    """Which typical plans of the structure the user executed, could still play, or lost."""
    out = await practice.plan_report(session, game_id)
    if out is None:
        raise HTTPException(status_code=404, detail="게임을 찾지 못했습니다.")
    return out


@router.get("/openings", response_model=list[OpeningCard])
async def openings(session: Session, username: str | None = None) -> list[OpeningCard]:
    return await openings_catalog.cards(session, username)


@router.get("/openings/{opening_id}", response_model=OpeningDetail)
async def opening(opening_id: str, session: Session, username: str | None = None) -> OpeningDetail:
    out = await openings_catalog.detail(session, opening_id, username)
    if out is None:
        raise HTTPException(status_code=404, detail="카탈로그에 없는 오프닝입니다.")
    return out


@router.get("/book", response_model=BookMoves)
def book(fen: Annotated[str, Query(min_length=10)]) -> BookMoves:
    """Next moves the opening book knows from this position (transpositions included)."""
    try:
        return openings_catalog.book_moves(fen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
