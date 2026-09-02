"""Maia endpoints: sparring moves, rating-conditioned move probabilities, backend status.

Thin: every endpoint delegates to services/maia.py. Handlers are sync because the
backends (torch inference, Stockfish) block; FastAPI runs them in its thread pool.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from chess_tutor.schemas import (
    MaiaStatus,
    MoveProbsResponse,
    SparringMoveRequest,
    SparringMoveResponse,
)
from chess_tutor.services import maia as maia_service

router = APIRouter(prefix="/maia", tags=["maia"])


@router.get("/_status")
def stub_status() -> dict[str, str]:
    return {"module": "maia", "status": maia_service.current_backend().name}


@router.get("/status", response_model=MaiaStatus)
def status() -> MaiaStatus:
    return MaiaStatus.model_validate(maia_service.status())


@router.post("/move", response_model=SparringMoveResponse)
def move(req: SparringMoveRequest) -> SparringMoveResponse:
    """Sample the reply a player of this rating would make in the given position."""
    try:
        san, uci, probs, source = maia_service.choose_move(req.fen, req.rating)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SparringMoveResponse(san=san, uci=uci, probs=probs, source=source)


@router.post("/probs", response_model=MoveProbsResponse)
def probs(req: SparringMoveRequest) -> MoveProbsResponse:
    """Probability of every legal move at this rating, highest first."""
    try:
        move_probs, source = maia_service.move_probs(req.fen, req.rating)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MoveProbsResponse(
        rating=req.rating,
        move_probs={san: round(p, maia_service.PRECISION) for san, p in move_probs.items()},
        source=source,
    )
