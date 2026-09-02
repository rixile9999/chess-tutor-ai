"""HTTP API. Thin: every endpoint delegates to a deterministic layer-2/4 function."""

from __future__ import annotations

import chess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from chess_tutor import __version__
from chess_tutor.motifs import detect
from chess_tutor.verify import Claim, Verdict, verify_all

app = FastAPI(title="chess-tutor", version=__version__)


class MotifRequest(BaseModel):
    fen: str
    san: str


class MotifResponse(BaseModel):
    fen_after: str
    motifs: list[dict[str, object]]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/motifs", response_model=MotifResponse)
def motifs(req: MotifRequest) -> MotifResponse:
    try:
        board = chess.Board(req.fen)
        move = board.parse_san(req.san)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    found = detect(board, move)
    after = board.copy()
    after.push(move)
    return MotifResponse(fen_after=after.fen(), motifs=[m.as_dict() for m in found])


@app.post("/verify", response_model=list[Verdict])
def verify(claims: list[Claim]) -> list[Verdict]:
    return verify_all(claims)
