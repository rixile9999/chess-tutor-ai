"""Coach for a live position: three hint levels and the after-move blunder check.

TODO(M7c): implement. Contract:
  hint(req) -> PlayHintResponse
    level 1: structure.classify + plans.match_plans (played moves from start_fen/moves_san);
             no move is named; text lists the structure and the side's plans
    level 2: level 1 + maia.move_probs top 2-3 as candidates with reasoning.explain_alternative
    level 3: level 2 + analysis.get_lines best line, motifs.detect, computer_move flag
    Every sentence's claims go through verify.verify_all; text keeps only verified sentences
    (verified/verified_claims/total_claims report the tally).
  check(req) -> PlayCheckResponse
    shallow (depth default 12) win-probability loss of `san` from fen_before, classified with
    analysis.classify_loss; best move from the cached lines; reason via explain_alternative;
    when the best move is a computer move at this rating, alternative_san is the most probable
    natural move whose loss is below the mistake threshold.
  Both raise ValueError for a bad FEN/SAN.
"""

from __future__ import annotations

from chess_tutor.schemas import (
    PlayCheckRequest,
    PlayCheckResponse,
    PlayHintRequest,
    PlayHintResponse,
)


def hint(req: PlayHintRequest) -> PlayHintResponse:
    raise NotImplementedError("play_coach.hint")


def check(req: PlayCheckRequest) -> PlayCheckResponse:
    raise NotImplementedError("play_coach.check")
