"""Claim verifier (layer 4 guard).

The verbalization layer must emit every board fact it relies on as a Claim. A claim
that does not hold on the board blocks the sentence that used it.
"""

from __future__ import annotations

from typing import Literal

import chess
from pydantic import BaseModel

ClaimKind = Literal["attacks", "defends", "is_check", "piece_on", "square_empty", "legal_move"]


class Claim(BaseModel):
    kind: ClaimKind
    fen: str
    subject: str | None = None
    """Square name for attacks/defends/piece_on/square_empty."""
    object: str | None = None
    """Square name (attacks/defends), piece symbol (piece_on) or SAN (legal_move)."""


class Verdict(BaseModel):
    claim: Claim
    holds: bool
    detail: str = ""


def _square(name: str | None) -> chess.Square:
    if name is None:
        raise ValueError("square required")
    return chess.parse_square(name)


def verify(claim: Claim) -> Verdict:
    board = chess.Board(claim.fen)
    try:
        match claim.kind:
            case "attacks":
                s, o = _square(claim.subject), _square(claim.object)
                holds = board.piece_at(s) is not None and o in board.attacks(s)
            case "defends":
                s, o = _square(claim.subject), _square(claim.object)
                ps, po = board.piece_at(s), board.piece_at(o)
                holds = (
                    ps is not None
                    and po is not None
                    and ps.color == po.color
                    and o in board.attacks(s)
                )
            case "is_check":
                holds = board.is_check()
            case "piece_on":
                piece = board.piece_at(_square(claim.subject))
                holds = piece is not None and piece.symbol() == claim.object
            case "square_empty":
                holds = board.piece_at(_square(claim.subject)) is None
            case "legal_move":
                if claim.object is None:
                    raise ValueError("SAN required")
                board.parse_san(claim.object)
                holds = True
    except ValueError as exc:
        return Verdict(claim=claim, holds=False, detail=str(exc))
    return Verdict(claim=claim, holds=holds)


def verify_all(claims: list[Claim]) -> list[Verdict]:
    return [verify(c) for c in claims]


def play_line(fen: str, sans: list[str]) -> chess.Board:
    """Play a SAN line and return the final board. Raises ValueError on an illegal move."""
    board = chess.Board(fen)
    for san in sans:
        board.push_san(san)
    return board
