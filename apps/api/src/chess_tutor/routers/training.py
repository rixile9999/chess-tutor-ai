"""Training endpoints: puzzles cut from the user's own games and their SM-2 schedule.

Thin by design; every rule lives in services/puzzles.py."""

from __future__ import annotations

from typing import Annotated

import chess.engine
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.db import get_session
from chess_tutor.models import Analysis, Game
from chess_tutor.schemas import (
    EngineLine,
    MoveAnalysis,
    PuzzleAttemptIn,
    PuzzleOut,
    TrainingSummary,
)
from chess_tutor.services import analysis as analysis_svc
from chess_tutor.services import puzzles as puzzle_service
from chess_tutor.services import users as user_svc

router = APIRouter(prefix="/training", tags=["training"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
Username = Annotated[str | None, Query(description="퍼즐을 가져올 사용자 이름")]

GAME_NOT_FOUND = "게임을 찾을 수 없습니다."
ANALYSIS_NOT_DONE = "완료된 엔진 분석이 없습니다. 먼저 이 게임을 분석한 뒤 다시 시도하세요."
ANALYSIS_UNREADABLE = "저장된 분석 결과를 읽을 수 없습니다. 게임을 다시 분석하세요."
PUZZLE_NOT_FOUND = "퍼즐을 찾을 수 없습니다."
USER_NOT_FOUND = "사용자를 찾을 수 없습니다. 먼저 이 이름으로 게임을 가져오세요."
NO_ENGINE = "퍼즐을 검증할 엔진을 찾을 수 없습니다. STOCKFISH_PATH를 확인해 주세요."
ENGINE_DIED = "엔진이 퍼즐 검증 도중 종료됐습니다. 잠시 후 다시 시도해 주세요."


def line_fetcher(depth: int) -> puzzle_service.LineFetcher:
    """Fresh engine lines at ``depth``, cached in engine_cache by FEN, engine, depth and
    multipv. One line is enough: the check only compares the best move."""

    async def fetch(fen: str) -> list[EngineLine]:
        return await analysis_svc.get_lines(fen, depth=depth, multipv=1)

    return fetch


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "training", "status": "ok"}


@router.post("/puzzles/from-game/{game_id}", response_model=list[PuzzleOut])
async def puzzles_from_game(
    game_id: int, session: SessionDep, username: Username = None
) -> list[PuzzleOut]:
    """Cut puzzles from the stored analysis of one game. Returns only the puzzles created by
    this call; positions the user already has are skipped, and so are candidates the engine
    disagrees with when it is re-asked at a depth of at least VERIFY_DEPTH."""
    game = await session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=GAME_NOT_FOUND)
    stmt = select(Analysis).where(Analysis.game_id == game_id)
    analysis = (await session.execute(stmt)).scalar_one_or_none()
    if analysis is None or analysis.status != "done":
        raise HTTPException(status_code=409, detail=ANALYSIS_NOT_DONE)
    try:
        moves = [MoveAnalysis.model_validate(move) for move in analysis.moves or []]
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail=ANALYSIS_UNREADABLE) from exc
    verify = line_fetcher(puzzle_service.verify_depth(analysis.depth))
    try:
        created = await puzzle_service.generate_from_game(
            session, game, moves, username=username, verify=verify
        )
    except user_svc.UserNotFound as exc:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND) from exc
    except chess.engine.EngineTerminatedError as exc:
        raise HTTPException(status_code=503, detail=ENGINE_DIED) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=NO_ENGINE) from exc
    return [puzzle_service.to_out(p) for p in created]


@router.get("/puzzles/due", response_model=list[PuzzleOut])
async def due_puzzles(
    session: SessionDep,
    username: Username = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[PuzzleOut]:
    found = await puzzle_service.due_puzzles(session, username=username, limit=limit)
    return [puzzle_service.to_out(p) for p in found]


@router.get("/puzzles/{puzzle_id}", response_model=PuzzleOut)
async def get_puzzle(puzzle_id: int, session: SessionDep) -> PuzzleOut:
    puzzle = await puzzle_service.get_puzzle(session, puzzle_id)
    if puzzle is None:
        raise HTTPException(status_code=404, detail=PUZZLE_NOT_FOUND)
    return puzzle_service.to_out(puzzle)


@router.post("/puzzles/{puzzle_id}/attempt", response_model=PuzzleOut)
async def attempt_puzzle(puzzle_id: int, body: PuzzleAttemptIn, session: SessionDep) -> PuzzleOut:
    """Record one attempt and return the rescheduled puzzle."""
    puzzle = await puzzle_service.get_puzzle(session, puzzle_id)
    if puzzle is None:
        raise HTTPException(status_code=404, detail=PUZZLE_NOT_FOUND)
    await puzzle_service.record_attempt(session, puzzle, body.correct, body.seconds)
    return puzzle_service.to_out(puzzle)


@router.get("/summary", response_model=TrainingSummary)
async def summary(session: SessionDep, username: Username = None) -> TrainingSummary:
    return await puzzle_service.training_summary(session, username=username)
