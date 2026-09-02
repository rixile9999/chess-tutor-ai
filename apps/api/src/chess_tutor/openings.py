"""Opening names from the lichess/chess-openings TSV (CC0). Lookup by position key so
transpositions resolve to the same name."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import chess


@dataclass(frozen=True)
class Opening:
    eco: str
    name: str
    pgn: str
    ply: int


def position_key(board: chess.Board) -> str:
    """FEN without move counters: piece placement, side to move, castling, en passant."""
    return " ".join(board.fen().split(" ")[:4])


@lru_cache
def _book() -> dict[str, Opening]:
    book: dict[str, Opening] = {}
    assets = resources.files("chess_tutor").joinpath("assets")
    for letter in "abcde":
        text = assets.joinpath(f"openings_{letter}.tsv").read_text(encoding="utf-8")
        for row in csv.DictReader(text.splitlines(), delimiter="\t"):
            board = chess.Board()
            try:
                for token in row["pgn"].split():
                    if token[0].isdigit():
                        continue
                    board.push_san(token)
            except ValueError:
                continue
            book[position_key(board)] = Opening(row["eco"], row["name"], row["pgn"], board.ply())
    return book


def lookup(board: chess.Board) -> Opening | None:
    return _book().get(position_key(board))


def classify_game(
    moves: list[chess.Move], start: chess.Board | None = None
) -> tuple[Opening | None, int]:
    """Return the deepest named opening reached and the ply where the game left the book.

    The second value is the 0-based index of the first unknown position, or len(moves) when
    every position is in the book."""
    board = (start or chess.Board()).copy()
    best: Opening | None = None
    book = _book()
    left_at = len(moves)
    for i, move in enumerate(moves):
        board.push(move)
        op = book.get(position_key(board))
        if op is not None:
            best = op
        elif best is not None and i - best.ply >= 3:
            left_at = i
            break
    if best is None:
        return None, 0
    return best, min(left_at, len(moves))
