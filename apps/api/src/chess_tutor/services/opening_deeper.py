"""더 깊이 (M8d-4, plan §10.4): engine lines and master statistics for one position.

Two read-only answers the explanation panel asks for **only when the user opens the section**,
so the cost is always a deliberate click:

* :func:`lines` — the engine's top continuations, through `services.analysis.get_lines`, which
  means the EngineCache row is shared with every other engine caller at the same
  (fen, engine, depth, multipv).
* :func:`masters` — the Lichess master explorer normalised into whole percents, through
  `services.openings_map.fetch_master_moves`, which means the per-process cache is shared with
  the master overlay on the candidate cards.

The master half never fails the page: a missing token or an unreachable explorer comes back as
``available=False`` with a Korean reason (plan §10.4), not as an error. Only the engine half,
which the user asked for explicitly, can answer 503.
"""

from __future__ import annotations

from typing import Any

import chess

from chess_tutor import schemas
from chess_tutor.config import get_settings
from chess_tutor.services import analysis, openings_map
from chess_tutor.services.opening_guide import BAD_FEN, ILLEGAL_POSITION

PV_CAP = 10
"""Plies of a principal variation kept. Enough to show the plan, short enough for one row."""

MASTER_CAP = 8
"""Moves kept from the explorer; it is asked for eight (openings_map.fetch_master_moves)."""

NO_TOKEN = "마스터 DB 연결 없음(LICHESS_TOKEN)"
EXPLORER_DOWN = "마스터 DB에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요."


def _board(fen: str) -> chess.Board:
    """The position, refused when it breaks the rules — Stockfish exits on such a FEN and the
    explorer has nothing to say about one. Same messages as the position guide."""
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"{BAD_FEN}: {exc}") from exc
    if not board.is_valid():
        raise ValueError(ILLEGAL_POSITION)
    return board


# ---------- engine lines ----------


def _line(entry: schemas.EngineLine) -> schemas.DeeperLine | None:
    """One cached engine line as the panel wants it, or None for a line with no moves (a mated
    or stalemated position still returns a score)."""
    if not entry.pv or not entry.pv_uci:
        return None
    return schemas.DeeperLine(
        san=entry.pv[0], uci=entry.pv_uci[0], score=entry.score, pv_san=entry.pv[:PV_CAP]
    )


async def lines(fen: str, depth: int, multipv: int) -> schemas.DeeperLines:
    """Top `multipv` continuations from `fen` at `depth`.

    Raises ValueError for a FEN no game can reach and RuntimeError (EngineBusy, a killed
    process, a missing binary) for anything the engine itself could not do."""
    board = _board(fen)
    normalised = board.fen()
    found = await analysis.get_lines(normalised, depth=depth, multipv=multipv)
    ordered = sorted(found, key=lambda entry: entry.rank)
    out = [line for line in (_line(entry) for entry in ordered) if line is not None]
    return schemas.DeeperLines(fen=normalised, depth=depth, lines=out[:multipv])


# ---------- master statistics ----------


def percentages(white: int, draws: int, black: int) -> tuple[int, int, int]:
    """White / draw / black shares as integers adding up to exactly 100 (largest remainder), so
    the stacked bar never leaves a gap. All zero when nobody has played the move."""
    total = white + draws + black
    if total <= 0:
        return (0, 0, 0)
    exact = [value * 100 / total for value in (white, draws, black)]
    out = [int(value) for value in exact]
    order = sorted(range(3), key=lambda i: exact[i] - out[i], reverse=True)
    for i in order[: 100 - sum(out)]:
        out[i] += 1
    return (out[0], out[1], out[2])


def _explorer_answered(fen: str) -> bool:
    """True when the explorer actually answered for this FEN.

    `fetch_master_moves` returns [] both for a failed call and for a position no master has
    reached, and only remembers the successful one, so its cache is what tells them apart —
    the difference between "연결 실패" and "기록 없음" on the screen."""
    return fen in openings_map._master_cache


def _move(board: chess.Board, row: dict[str, Any]) -> schemas.MasterMove | None:
    """One explorer row as a master move, or None when its UCI is not legal here."""
    try:
        move = chess.Move.from_uci(str(row.get("uci", "")))
    except ValueError:
        return None
    if move not in board.legal_moves:
        return None
    white, draws, black = (int(row.get(key, 0) or 0) for key in ("white", "draws", "black"))
    shares = percentages(white, draws, black)
    rating = row.get("averageRating")
    return schemas.MasterMove(
        san=board.san(move),
        uci=move.uci(),
        games=white + draws + black,
        white=shares[0],
        draws=shares[1],
        black=shares[2],
        avg_rating=int(rating) if isinstance(rating, int | float) else None,
    )


def masters(fen: str) -> schemas.MasterStats:
    """Master statistics for one position, busiest move first.

    Blocking (httpx with a three-second timeout): call it from a worker thread. Raises
    ValueError for a FEN no game can reach; everything else is reported in the answer."""
    board = _board(fen)
    normalised = board.fen()
    token = get_settings().lichess_token
    if token is None:
        return schemas.MasterStats(fen=normalised, available=False, reason=NO_TOKEN)
    rows = openings_map.fetch_master_moves(normalised, token)
    if not rows and not _explorer_answered(normalised):
        return schemas.MasterStats(fen=normalised, available=False, reason=EXPLORER_DOWN)
    out = [move for move in (_move(board, row) for row in rows) if move is not None]
    out.sort(key=lambda move: (-move.games, move.san))
    return schemas.MasterStats(fen=normalised, available=True, moves=out[:MASTER_CAP])
