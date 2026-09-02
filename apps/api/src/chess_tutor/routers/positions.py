"""Stateless position endpoints: motifs and claim verification."""

from __future__ import annotations

import chess
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from chess_tutor.motifs import detect
from chess_tutor.schemas import MotifOut
from chess_tutor.verify import Claim, Verdict, verify_all

router = APIRouter(prefix="/positions", tags=["positions"])


class MotifRequest(BaseModel):
    fen: str
    san: str


class MotifResponse(BaseModel):
    fen_after: str
    motifs: list[MotifOut]


@router.post("/motifs", response_model=MotifResponse)
def motifs(req: MotifRequest) -> MotifResponse:
    try:
        board = chess.Board(req.fen)
        move = board.parse_san(req.san)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    found = detect(board, move)
    after = board.copy()
    after.push(move)
    return MotifResponse(
        fen_after=after.fen(), motifs=[MotifOut.model_validate(m.as_dict()) for m in found]
    )


@router.post("/verify", response_model=list[Verdict])
def verify(claims: list[Claim]) -> list[Verdict]:
    return verify_all(claims)
