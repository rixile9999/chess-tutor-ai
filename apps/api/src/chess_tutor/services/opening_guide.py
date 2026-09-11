"""What the opening map knows about one position (layer 3, plan §5.1-§5.2).

Two stateless answers, no engine and no database:

* :func:`candidates` — every continuation the bundled opening book knows from here, each with
  the position it reaches, the name that position (or its line) carries, and how many moves
  are still forced before a named position shows up. With ``masters`` and a Lichess token the
  master explorer is laid over the same list, and the moves masters play that the book does
  not know join it as ``master_only`` entries.
* :func:`guide` — those candidates plus the position's own book name, its pawn structure
  (`structure.classify`) and the progress of every system setup (`services.setups`).

Book work is cached per position key, so walking a line back and forth costs nothing after the
first visit; the master overlay keeps its own per-process cache in
`services.openings_map.fetch_master_moves` and stays outside ours, so a failed explorer call is
never remembered as "no master games".

Names are Korean (`openings.name_ko`, plan §10.5); a candidate also carries the book's English
`name_en`, which is what the card shows on hover and what `openings.find_rows` is keyed by.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import chess

from chess_tutor.config import get_settings
from chess_tutor.openings import lookup, name_ko, next_moves, position_key
from chess_tutor.schemas import Color, NamedCandidate, PositionGuide, SetupStatus
from chess_tutor.services import setups
from chess_tutor.services.openings_map import fetch_master_moves, move_label
from chess_tutor.structure import classify

CACHE_SIZE = 4096
TO_NAME_PLIES = 4
"""How far a forced line is followed looking for a named position (plan §5.2)."""

BAD_FEN = "FEN을 읽을 수 없습니다"
ILLEGAL_POSITION = "체스 규칙에 맞지 않는 국면입니다."


def _board(fen: str) -> chess.Board:
    """The position, refused when it breaks the rules. Raises ValueError either way."""
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"{BAD_FEN}: {exc}") from exc
    if not board.is_valid():
        raise ValueError(ILLEGAL_POSITION)
    return board


def _side(board: chess.Board) -> Color:
    return "white" if board.turn == chess.WHITE else "black"


def _normalised_fen(key: str, ply: int) -> str:
    """A FEN for a position key at a given ply. The key drops the move counters, but the move
    label needs the move number, so the cache is keyed by both and the board rebuilt from them."""
    return f"{key} 0 {ply // 2 + 1}"


def _to_name(board: chess.Board) -> list[str]:
    """SAN moves from here to the next named position while the book offers exactly one
    continuation, or [] when the line branches or stays unnamed for TO_NAME_PLIES plies."""
    out: list[str] = []
    walk = board.copy(stack=False)
    for _ in range(TO_NAME_PLIES):
        moves = next_moves(walk)
        if len(moves) != 1:
            return []
        move, _opening = moves[0]
        out.append(walk.san(move))
        walk.push(move)
        if lookup(walk) is not None:
            return out
    return []


@lru_cache(maxsize=CACHE_SIZE)
def _book_candidates(key: str, ply: int) -> tuple[NamedCandidate, ...]:
    board = chess.Board(_normalised_fen(key, ply))
    out: list[NamedCandidate] = []
    for move, opening in next_moves(board):
        san = board.san(move)
        after = board.copy(stack=False)
        after.push(move)
        named_here = lookup(after) is not None
        out.append(
            NamedCandidate(
                san=san,
                uci=move.uci(),
                label=move_label(ply + 1, san),
                fen_after=after.fen(),
                name=name_ko(opening.name),
                name_en=opening.name,
                eco=opening.eco,
                named_here=named_here,
                to_name=[] if named_here else _to_name(after),
            )
        )
    return tuple(out)


@lru_cache(maxsize=CACHE_SIZE)
def _cached_setups(key: str) -> tuple[SetupStatus, ...]:
    return tuple(setups.status_all(chess.Board(f"{key} 0 1")))


def _master_score(row: dict[str, Any], color: Color) -> tuple[int, float]:
    """Games and score of one explorer row from `color`'s point of view; the same formula as
    the map's master overlay (openings_map._score)."""
    white, draws, black = (int(row.get(key, 0) or 0) for key in ("white", "draws", "black"))
    total = white + draws + black
    wins = white if color == "white" else black
    return total, round((wins + 0.5 * draws) / total, 4) if total else 0.0


def _with_masters(
    board: chess.Board, book: list[NamedCandidate], color: Color
) -> list[NamedCandidate]:
    """Lay the master explorer over the book candidates and append the moves only masters play.

    Silent when no token is configured, and unchanged when the explorer is unreachable: the
    overlay is an extra, never a reason for the page to fail (plan §5.5)."""
    token = get_settings().lichess_token
    if token is None:
        return book
    rows = {str(row.get("uci", "")): row for row in fetch_master_moves(board.fen(), token)}
    if not rows:
        return book
    known = {candidate.uci: candidate for candidate in book}
    for uci, candidate in known.items():
        row = rows.get(uci)
        if row is not None:
            candidate.master_games, candidate.master_score = _master_score(row, color)
    ply = board.ply()
    for uci, row in rows.items():
        if uci in known:
            continue
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            continue
        if move not in board.legal_moves:
            continue
        san = board.san(move)
        after = board.copy(stack=False)
        after.push(move)
        here = lookup(after)
        games, score = _master_score(row, color)
        book.append(
            NamedCandidate(
                san=san,
                uci=uci,
                label=move_label(ply + 1, san),
                fen_after=after.fen(),
                name=name_ko(here.name) if here is not None else "",
                name_en=here.name if here is not None else None,
                eco=here.eco if here is not None else "",
                named_here=here is not None,
                master_games=games,
                master_score=score,
                master_only=True,
            )
        )
    return book


def candidates(
    board: chess.Board, color: Color = "white", masters: bool = False
) -> list[NamedCandidate]:
    """Book continuations from this position, master statistics laid over them when asked.

    Sorted the way the cards are shown: named positions first, then the busiest master move,
    then SAN."""
    out = [
        entry.model_copy(deep=True) for entry in _book_candidates(position_key(board), board.ply())
    ]
    if masters:
        out = _with_masters(board, out, color)
    out.sort(key=lambda c: (not c.named_here, -(c.master_games or 0), c.san))
    return out


def guide(fen: str, color: Color = "white", masters: bool = False) -> PositionGuide:
    """Candidates, book name, pawn structure and setup progress for one position.

    `color` is the user's side and only decides whose point of view the master score is from.
    `name` is Korean; the book's own English name stays next to it as `name_en`, here and
    on every candidate.
    Raises ValueError for a FEN that is not a legal position."""
    board = _board(fen)
    here = lookup(board)
    return PositionGuide(
        fen=board.fen(),
        side=_side(board),
        name=name_ko(here.name) if here is not None else None,
        name_en=here.name if here is not None else None,
        eco=here.eco if here is not None else None,
        in_book=here is not None,
        structure=classify(board),
        candidates=candidates(board, color, masters),
        setups=[entry.model_copy(deep=True) for entry in _cached_setups(position_key(board))],
    )


def clear_cache() -> None:
    """Drop the per-position caches (tests that change the book or the token)."""
    _book_candidates.cache_clear()
    _cached_setups.cache_clear()
