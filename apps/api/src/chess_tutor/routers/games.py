"""Game import and listing endpoints. Thin: parsing and storage live in services.games,
platform fetches in services.importers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.db import get_session
from chess_tutor.schemas import (
    GameDetail,
    GameSummary,
    ImportChesscomRequest,
    ImportLichessRequest,
    ImportPGNRequest,
    ImportResult,
)
from chess_tutor.services import games as games_svc
from chess_tutor.services import importers

router = APIRouter(prefix="/games", tags=["games"])

Session = Annotated[AsyncSession, Depends(get_session)]

NOT_FOUND = "게임을 찾지 못했습니다."


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "games", "status": "ok"}


@router.post("/import/pgn", response_model=ImportResult)
async def import_pgn(req: ImportPGNRequest, session: Session) -> ImportResult:
    report = games_svc.parse_pgn(req.pgn)
    if not report.games and not report.errors:
        raise HTTPException(status_code=422, detail="PGN에서 게임을 찾지 못했습니다.")
    result = await games_svc.upsert_games(session, report.games, "pgn", username=req.username)
    result.skipped += len(report.errors)
    result.errors = report.errors
    return result


@router.post("/import/chesscom", response_model=ImportResult)
async def import_chesscom(req: ImportChesscomRequest, session: Session) -> ImportResult:
    try:
        return await importers.import_chesscom(session, req.username, req.months)
    except importers.FetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/import/lichess", response_model=ImportResult)
async def import_lichess(req: ImportLichessRequest, session: Session) -> ImportResult:
    try:
        return await importers.import_lichess(session, req.username, req.max_games)
    except importers.FetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("", response_model=list[GameSummary])
async def list_games(
    session: Session,
    user: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[GameSummary]:
    rows = await games_svc.list_games(session, username=user, limit=limit, offset=offset)
    return [games_svc.to_summary(g) for g in rows]


@router.get("/{game_id}", response_model=GameDetail)
async def get_game(game_id: int, session: Session) -> GameDetail:
    game = await games_svc.get_game(session, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return games_svc.to_detail(game)


@router.delete("/{game_id}", status_code=204, response_class=Response)
async def delete_game(game_id: int, session: Session) -> Response:
    if not await games_svc.delete_game(session, game_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=204)
