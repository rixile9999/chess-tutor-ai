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


def san_tokens(pgn: str) -> list[str]:
    """Move tokens of a TSV line, move numbers dropped."""
    return [token for token in pgn.split() if not token[0].isdigit()]


def moves_of(row: Opening) -> list[chess.Move]:
    """The line of one TSV row as moves. Empty when the row does not replay (never happens
    for the bundled files; :func:`rows` drops those)."""
    board = chess.Board()
    moves: list[chess.Move] = []
    try:
        for token in san_tokens(row.pgn):
            move = board.parse_san(token)
            moves.append(move)
            board.push(move)
    except ValueError:
        return []
    return moves


@lru_cache
def rows() -> tuple[Opening, ...]:
    """Every TSV line that replays, in file order (a..e). `ply` is the length of the line."""
    out: list[Opening] = []
    assets = resources.files("chess_tutor").joinpath("assets")
    for letter in "abcde":
        text = assets.joinpath(f"openings_{letter}.tsv").read_text(encoding="utf-8")
        for row in csv.DictReader(text.splitlines(), delimiter="\t"):
            board = chess.Board()
            try:
                for token in san_tokens(row["pgn"]):
                    board.push_san(token)
            except ValueError:
                continue
            out.append(Opening(row["eco"], row["name"], row["pgn"], board.ply()))
    return tuple(out)


def find_rows(eco: str, name: str, ply: int | None = None) -> list[Opening]:
    """TSV rows with this exact (eco, name), narrowed to one line length when `ply` is given.

    The catalogue in services/openings_catalog.py addresses rows this way: several rows can
    share a name (the same variation entered by different move orders), so the length of the
    line is the disambiguator."""
    return [
        row
        for row in rows()
        if row.eco == eco and row.name == name and (ply is None or row.ply == ply)
    ]


@lru_cache
def _book() -> dict[str, Opening]:
    book: dict[str, Opening] = {}
    for row in rows():
        board = chess.Board()
        for token in san_tokens(row.pgn):
            board.push_san(token)
        book[position_key(board)] = row
    return book


@lru_cache
def _tree() -> dict[str, dict[str, Opening]]:
    """position key -> uci -> the opening that move leads to.

    Built from every prefix of every TSV line, so a position reached by a different move order
    finds the same continuations. The name attached to a move is the name of the position it
    reaches when that position is itself in the book, and the name of the row that contributed
    the move otherwise; when several rows offer the same move the shortest name wins, which is
    the most general one ("Sicilian Defense" over "Sicilian Defense: Najdorf Variation")."""
    book = _book()
    tree: dict[str, dict[str, Opening]] = {}
    for row in rows():
        board = chess.Board()
        for token in san_tokens(row.pgn):
            move = board.parse_san(token)
            key = position_key(board)
            board.push(move)
            named = book.get(position_key(board), row)
            current = tree.setdefault(key, {})
            known = current.get(move.uci())
            if known is None or (len(named.name), named.name) < (len(known.name), known.name):
                current[move.uci()] = named
    return tree


def next_moves(board: chess.Board) -> list[tuple[chess.Move, Opening]]:
    """Book continuations from this position, sorted by the name they lead to."""
    entries = _tree().get(position_key(board), {})
    out = [(chess.Move.from_uci(uci), op) for uci, op in entries.items()]
    out.sort(key=lambda pair: (pair[1].name, pair[0].uci()))
    return [(move, op) for move, op in out if board.is_legal(move)]


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
