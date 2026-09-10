"""Practice games on disk: PGN assembly, the `practice` Game row, and the plan report.

TODO(M7a/M7b): implement. Contract:
  save(session, req) -> PracticeGameOut
    Builds a PGN (headers: Event "chess-tutor practice", Site "chess-tutor", Date, Round "-",
    White/Black = username or the opponent's display name ("Maia 1500", "Stockfish 1800",
    "수동" for a manual game), WhiteElo/BlackElo from the opponent spec, Result, FEN/SetUp when
    start_fen is not the standard start, Opening/ECO from openings.classify_game, plus
    Mode ("manual" | "ai-white" | "ai-black"), Opponent ("maia:1500"), CoachPreset, Hints,
    Takebacks, Alerts, PracticeMode, OpeningId, Termination; %clk comments when clocks given).
    Validates every SAN from start_fen (ValueError otherwise). Stores through
    games.parse_pgn + games.upsert_games(session, parsed, source="practice", username) so the
    user gets platform "local" and user_color follows the PGN names. Commits. When
    req.analyse, queues analysis.submit_analysis(game_id). Returns game_id, analysis status, pgn.
  plan_report(session, game_id) -> PlanReport | None
    None when the game does not exist. Needs a finished analysis (analysis.get_or_analyze);
    picks the user's side (user_color, else White), classifies the structure at the tabiya /
    first middlegame position, runs plans.match_plans over the whole game with the analysis
    PVs, lists executed / pv_match / later / unavailable, and the pawn breaks the side played
    (plans.break_hints + the move number). summary is Korean, built only from those fields.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.schemas import PlanReport, PracticeGameIn, PracticeGameOut


async def save(session: AsyncSession, req: PracticeGameIn) -> PracticeGameOut:
    raise NotImplementedError("practice.save")


async def plan_report(session: AsyncSession, game_id: int) -> PlanReport | None:
    raise NotImplementedError("practice.plan_report")
