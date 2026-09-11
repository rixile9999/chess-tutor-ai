"""Opening names from the lichess/chess-openings TSV (CC0). Lookup by position key so
transpositions resolve to the same name.

The English name is the identity of a line — the TSV column, the catalogue's `tsv_name`, the
key of :func:`find_rows` — and :func:`name_ko` is the Korean label shown next to it
(``assets/opening_names_ko.json``, built by ``scripts/translate_openings.py``, plan §10.5).
Nothing looks an opening up by its Korean name."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

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


@lru_cache
def names_ko() -> dict[str, str]:
    """English name -> Korean name, from the bundled asset. Empty when the file is missing."""
    asset = resources.files("chess_tutor").joinpath("assets/opening_names_ko.json")
    if not asset.is_file():
        return {}
    loaded: Any = json.loads(asset.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return {}
    return {str(key): str(value) for key, value in loaded.items() if str(value).strip()}


def name_ko(name: str) -> str:
    """The Korean name of an opening, the English one when it has no entry (plan §10.5).

    Every name in the bundled TSVs has one (`test_openings.py` pins that); the fallback is for a
    name that comes from somewhere else — a master-explorer line, a newer book."""
    return names_ko().get(name, name)


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


def warm() -> None:
    """Build the TSV tables now rather than on the first request.

    Parsing the 3,810 lines and every prefix of them takes about 2.5 s, which is what the first
    ``GET /play/book`` used to pay. The API startup runs this on a thread; the caches are
    ``lru_cache``, so a request arriving meanwhile just builds it itself and both get the same
    table. The Korean names are read here too — one 350 kB JSON file, a few milliseconds."""
    _tree()
    names_ko()


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
    every position is in the book.

    A game that starts from a position the book already names (a practice game from a tabiya,
    an imported "from position" game) takes its name from that position, so it is classified
    even when none of its own moves reaches a named line. Book depth is compared in absolute
    plies (`start.ply()` plus the index), because a row's `ply` counts from the standard start
    while `i` counts from `start`."""
    board = (start or chess.Board()).copy()
    book = _book()
    base = board.ply()
    best: Opening | None = book.get(position_key(board))
    left_at = len(moves)
    for i, move in enumerate(moves):
        board.push(move)
        op = book.get(position_key(board))
        if op is not None:
            best = op
        elif best is not None and base + i - best.ply >= 3:
            left_at = i
            break
    if best is None:
        return None, 0
    return best, min(left_at, len(moves))
