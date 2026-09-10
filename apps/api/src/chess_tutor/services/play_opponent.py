"""The other side of a practice game: Maia at a rating or Stockfish at a UCI_Elo.

TODO(M7a): implement. Contract:
  choose(req) -> PlayMoveResponse
    kind == "maia":       maia.choose_move(fen, rating clamped to 1100..2000, seed=req.seed,
                          opp_rating=req.user_rating) -> source is the backend that answered
    kind == "stockfish":  engine.PlayEngine (private instance, UCI_LimitStrength + UCI_Elo
                          clamped to 1320..3190, movetime) -> source "stockfish", probs {}
  Raises ValueError for a bad FEN or a finished game.
"""

from __future__ import annotations

from chess_tutor.schemas import PlayMoveRequest, PlayMoveResponse


def choose(req: PlayMoveRequest) -> PlayMoveResponse:
    raise NotImplementedError("play_opponent.choose")
