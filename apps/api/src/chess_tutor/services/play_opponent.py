"""The other side of a practice game: Maia at a rating or Stockfish at a UCI_Elo.

  choose(req) -> PlayMoveResponse
    kind == "maia":       maia.choose_move(fen, rating clamped to 1100..2000, seed=req.seed,
                          opp_rating=req.user_rating) -> source is the backend that answered
    kind == "stockfish":  engine.PlayEngine (private instance, UCI_LimitStrength + UCI_Elo
                          clamped to 1320..3190, movetime) -> source "stockfish", probs {}
  Raises ValueError for a bad FEN or a finished game.

A missing Stockfish binary is not an error here: the request falls back to the Maia backend
chain and reports the source that actually answered, so a game never stalls because the user
picked an opponent this machine cannot run. Only the FEN and the position itself are errors.
"""

from __future__ import annotations

import logging
import threading
import time

import chess

from chess_tutor.config import get_settings
from chess_tutor.engine import PlayEngine
from chess_tutor.schemas import PlayMoveRequest, PlayMoveResponse
from chess_tutor.services import maia

log = logging.getLogger(__name__)

MAIA_MIN, MAIA_MAX = 1100, 2000
"""Rating buckets Maia-2 was trained on; outside them it is asked for the nearest bucket."""
STOCKFISH_MIN, STOCKFISH_MAX = 1320, 3190
"""UCI_Elo range of Stockfish 16+."""

_engine: PlayEngine | None = None
_engine_lock = threading.Lock()
"""Guards creation only; PlayEngine serialises its own searches."""
_engine_error: str | None = None
"""Why the private engine could not start, so the fallback is not retried on every move."""


def clamp(rating: int, low: int, high: int) -> int:
    return max(low, min(high, rating))


def get_engine() -> PlayEngine:
    """The process that plays the Stockfish opponent, started on first use.

    Raises RuntimeError when there is no binary; the caller falls back to Maia."""
    global _engine, _engine_error
    with _engine_lock:
        if _engine is None:
            if _engine_error is not None:
                raise RuntimeError(_engine_error)
            try:
                _engine = PlayEngine()
            except Exception as exc:  # noqa: BLE001 - any start-up failure means "no engine"
                _engine_error = str(exc)
                raise RuntimeError(_engine_error) from exc
        return _engine


def close_engine() -> None:
    """Quit the private engine (tests, shutdown). The next call starts a new one."""
    global _engine, _engine_error
    with _engine_lock:
        if _engine is not None:
            _engine.close()
        _engine = None
        _engine_error = None


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _maia_move(req: PlayMoveRequest, started: float) -> PlayMoveResponse:
    rating = clamp(req.opponent.rating, MAIA_MIN, MAIA_MAX)
    san, uci, probs, source = maia.choose_move(
        req.fen, rating, seed=req.seed, opp_rating=req.user_rating
    )
    return PlayMoveResponse(
        san=san, uci=uci, source=source, probs=probs, think_ms=_elapsed_ms(started)
    )


def _stockfish_move(req: PlayMoveRequest, started: float) -> PlayMoveResponse:
    """A move from the private weakened engine. Raises RuntimeError when it cannot run."""
    board = chess.Board(req.fen)
    if not any(board.legal_moves):
        raise ValueError("no legal moves in this position")
    rating = clamp(req.opponent.rating, STOCKFISH_MIN, STOCKFISH_MAX)
    movetime = get_settings().play_movetime_seconds
    play_engine = get_engine()
    try:
        move = play_engine.play(board, movetime=movetime, elo=rating)
    except Exception as exc:  # noqa: BLE001 - a dead process must not poison later requests
        close_engine()
        raise RuntimeError(f"stockfish failed: {exc}") from exc
    return PlayMoveResponse(
        san=board.san(move),
        uci=move.uci(),
        source="stockfish",
        probs={},
        think_ms=_elapsed_ms(started),
    )


def choose(req: PlayMoveRequest) -> PlayMoveResponse:
    """The opponent's move in this position, with the source that produced it."""
    started = time.perf_counter()
    if req.opponent.kind == "stockfish":
        try:
            return _stockfish_move(req, started)
        except RuntimeError as exc:
            # No binary, or the process died: answer with the human-like backend instead of
            # failing the move. The response says which source really played.
            log.warning("stockfish opponent unavailable, falling back to maia: %s", exc)
    return _maia_move(req, started)
