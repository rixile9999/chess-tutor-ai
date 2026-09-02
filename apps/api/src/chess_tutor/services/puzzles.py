"""Puzzles cut from the user's own games, scheduled with SM-2 (layer 5: personalization).

A puzzle hides in every mistake or blunder whose punishment is concrete: in the position the
error left behind, the engine's best line either mates or swings the evaluation by at least
SWING_CP centipawns for the side to move, and that line shows at least the reply (two plies).
The solver plays the punishing side, which is why only the *opponent's* errors become puzzles
once the user's colour is known: a puzzle cut from the user's own blunder would hand them the
other colour and ask them to punish themselves.

Cutting is deterministic: no LLM, and every move stored as a solution has been replayed on the
board with python-chess. It is not, however, trustworthy on its own. The stored line comes from
whatever depth the game was analysed at, and a shallow line can recommend a move that loses on
the spot, so ``generate_from_game`` takes a ``verify`` fetcher and drops any puzzle a deeper
look disagrees with (see ``verified``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import chess
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from chess_tutor.models import Game, Puzzle, PuzzleAttempt, User
from chess_tutor.motifs import detect
from chess_tutor.schemas import (
    Color,
    EngineLine,
    MotifMiss,
    MoveAnalysis,
    PuzzleOut,
    Score,
    TrainingSummary,
)
from chess_tutor.services import users

LineFetcher = Callable[[str], Awaitable[list[EngineLine]]]
"""Fresh engine lines for a FEN. ``services.analysis.get_lines`` bound to a depth."""

PUZZLE_CLASSIFICATIONS = frozenset({"mistake", "blunder"})
SWING_CP = 150
"""Minimum evaluation gain for the solver, in centipawns, unless the line mates."""
MIN_LINE_PLIES = 2
MAX_SOLUTION_PLIES = 4
MATE_CP = 10_000
"""Centipawn stand-in for a mate score when computing swings."""
VERIFY_DEPTH = 18
"""Floor for the re-check of a candidate puzzle. Games are often analysed shallower than this
(the default is 16, a quick pass can be 8), and a shallow line is exactly how a move that loses
to a two-move mate ended up stored as a solution."""

# SM-2 constants
DEFAULT_EASE = 2.5
MIN_EASE = 1.3
MAX_EASE = 3.0
EASE_GAIN = 0.1
EASE_LOSS = 0.2
FIRST_INTERVALS_DAYS = (1.0, 6.0)
LAPSE_DELAY = timedelta(minutes=10)


def now_utc() -> datetime:
    """Naive UTC, the same convention as models.utcnow, so DateTime comparisons line up."""
    return datetime.now(UTC).replace(tzinfo=None)


def position_key(fen: str) -> str:
    """FEN without move counters: the same position reached in two games is one puzzle."""
    return " ".join(fen.split(" ")[:4])


def orientation_of(fen: str) -> Color:
    """The solver is the side to move in the puzzle position."""
    return "white" if chess.Board(fen).turn == chess.WHITE else "black"


# ---------- scoring helpers ----------


def cp_for(score: Score, color: chess.Color) -> int:
    """Centipawns from ``color``'s point of view. A mate counts as +/-MATE_CP."""
    if score.mate:
        cp = MATE_CP if score.mate > 0 else -MATE_CP
    else:
        cp = score.cp or 0
    return cp if color == chess.WHITE else -cp


def mates_for(score: Score, color: chess.Color) -> bool:
    """True when the score is a mate delivered by ``color``."""
    mate = score.mate
    return mate is not None and mate != 0 and (mate > 0) == (color == chess.WHITE)


def is_concrete(eval_before: Score, best: EngineLine, solver: chess.Color) -> bool:
    """A punishment is concrete when the best line mates or gains SWING_CP for the solver
    compared with the evaluation before the error, and shows at least MIN_LINE_PLIES."""
    if len(best.pv_uci) < MIN_LINE_PLIES:
        return False
    if mates_for(best.score, solver):
        return True
    return cp_for(best.score, solver) - cp_for(eval_before, solver) >= SWING_CP


# ---------- cutting puzzles ----------


def legal_prefix(board: chess.Board, pv_uci: list[str], limit: int) -> list[str]:
    """The longest prefix of the line that is legal from ``board``, at most ``limit`` plies."""
    moves: list[str] = []
    scratch = board.copy()
    for uci in pv_uci[:limit]:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in scratch.legal_moves:
            break
        moves.append(move.uci())
        scratch.push(move)
    return moves


def solution_from_line(board: chess.Board, pv_uci: list[str]) -> list[str]:
    """Solver's move first, at most MAX_SOLUTION_PLIES, ending on the solver's own move so the
    puzzle stops once the idea is shown. Empty when fewer than MIN_LINE_PLIES are legal."""
    moves = legal_prefix(board, pv_uci, MAX_SOLUTION_PLIES)
    if len(moves) < MIN_LINE_PLIES:
        return []
    if len(moves) % 2 == 0:
        moves = moves[:-1]
    return moves


def best_line(move: MoveAnalysis) -> EngineLine | None:
    return min(move.lines, key=lambda line: line.rank) if move.lines else None


def cut_puzzle(error: MoveAnalysis, reply: MoveAnalysis) -> Puzzle | None:
    """The puzzle hidden in one analysed error.

    ``error`` is the mistake or blunder; ``reply`` is the analysis of the following ply, whose
    lines describe the position the error left behind. Returns an unsaved Puzzle (no user or
    game yet) or None when the punishment is not concrete."""
    if error.classification not in PUZZLE_CLASSIFICATIONS:
        return None
    best = best_line(reply)
    if best is None or position_key(reply.fen_before) != position_key(error.fen_after):
        return None
    try:
        board = chess.Board(error.fen_after)
    except ValueError:
        return None
    if not is_concrete(error.eval_before, best, board.turn):
        return None
    solution = solution_from_line(board, best.pv_uci)
    if not solution:
        return None
    motifs = detect(board, chess.Move.from_uci(solution[0]))
    return Puzzle(
        ply=error.ply,
        fen=error.fen_after,
        solution=solution,
        motif=motifs[0].kind if motifs else None,
        source="own",
        due_at=now_utc(),
        interval_days=0.0,
        ease=DEFAULT_EASE,
        reps=0,
        lapses=0,
    )


def cut_puzzles(
    analysis_moves: list[MoveAnalysis], user_color: Color | None = None
) -> list[Puzzle]:
    """Every concrete puzzle in an analysed game, in ply order, without dedupe.

    ``user_color`` is the colour the deck's owner played. The solver of a puzzle is the side
    that punishes the error, so only the opponent's errors are cut: that keeps every puzzle on
    the user's own colour. With no colour known (a game imported without a username) every
    error is cut, as before."""
    by_ply = {move.ply: move for move in analysis_moves}
    found: list[Puzzle] = []
    for error in sorted(analysis_moves, key=lambda move: move.ply):
        if user_color is not None and error.color == user_color:
            continue
        reply = by_ply.get(error.ply + 1)
        if reply is None:
            continue
        puzzle = cut_puzzle(error, reply)
        if puzzle is not None:
            found.append(puzzle)
    return found


# ---------- verification ----------


def verify_depth(analysis_depth: int | None) -> int:
    """Depth for the re-check: never shallower than the analysis, never below VERIFY_DEPTH."""
    return max(analysis_depth or 0, VERIFY_DEPTH)


def verified(puzzle: Puzzle, lines: list[EngineLine]) -> bool:
    """True when a deeper look at the puzzle position still backs the stored solution.

    ``lines`` are fresh engine lines for ``puzzle.fen``. Two ways a stored puzzle turns out
    wrong, both seen in real data:

    1. the solver is the one getting mated, so there is nothing to punish;
    2. the stored first move is not the best move at all. A shallow line once offered a knight
       recapture that walks into mate in two, presented to the user as the answer.

    Anything the engine cannot speak to (no lines, no pv) is dropped rather than trusted."""
    solution: list[str] = [str(uci) for uci in puzzle.solution or []]
    if not solution or not lines:
        return False
    best = min(lines, key=lambda line: line.rank)
    if not best.pv_uci:
        return False
    solver = chess.Board(puzzle.fen).turn
    if mates_for(best.score, not solver):
        return False
    return best.pv_uci[0] == solution[0]


# ---------- persistence ----------


async def resolve_user_id(session: AsyncSession, username: str | None) -> int | None:
    """The primary account with this username, resolved the same way the profile resolves it.

    Raises users.UserNotFound for an unknown name instead of quietly creating an account: a
    'Duke' minted from a query parameter used to own puzzles that /profile/duke counted but
    /training/puzzles/due never showed."""
    if not username:
        return None
    return (await users.require_user(session, username)).id


def _user_clause(username: str | None) -> ColumnElement[bool] | None:
    """Puzzles of every account with this username (chess.com and lichess names may coexist,
    and the same name in another case is the same person)."""
    if not username:
        return None
    return Puzzle.user_id.in_(select(User.id).where(users.matches(username)))


def user_color_of(game: Game, username: str | None) -> Color | None:
    """Which colour the deck's owner played in this game, or None when that is unknown."""
    if game.user_color in ("white", "black"):
        return game.user_color  # type: ignore[return-value]
    if username:
        name = users.normalise(username)
        if (game.white or "").strip().lower() == name:
            return "white"
        if (game.black or "").strip().lower() == name:
            return "black"
    return None


async def generate_from_game(
    session: AsyncSession,
    game: Game,
    analysis_moves: list[MoveAnalysis],
    username: str | None = None,
    verify: LineFetcher | None = None,
) -> list[Puzzle]:
    """Cut, dedupe (same position, same user), verify and store the puzzles of one analysed game.

    Returns only the puzzles created by this call; positions the user already owns are skipped.
    ``verify`` fetches fresh engine lines for a candidate's position; when it is given, a puzzle
    the engine disagrees with is dropped instead of stored. Passing None skips that check and
    stores whatever the stored analysis claimed, which is only safe in tests."""
    user_id = await resolve_user_id(session, username) if username else game.user_id
    owner = Puzzle.user_id == user_id if user_id is not None else Puzzle.user_id.is_(None)
    existing = (await session.execute(select(Puzzle.fen).where(owner))).scalars()
    seen = {position_key(fen) for fen in existing}

    created: list[Puzzle] = []
    for puzzle in cut_puzzles(analysis_moves, user_color=user_color_of(game, username)):
        key = position_key(puzzle.fen)
        if key in seen:
            continue
        seen.add(key)
        if verify is not None and not verified(puzzle, await verify(puzzle.fen)):
            continue
        puzzle.user_id = user_id
        puzzle.game_id = game.id
        created.append(puzzle)
    session.add_all(created)
    await session.commit()
    return created


async def get_puzzle(session: AsyncSession, puzzle_id: int) -> Puzzle | None:
    return await session.get(Puzzle, puzzle_id)


async def due_puzzles(
    session: AsyncSession,
    username: str | None = None,
    limit: int = 20,
    now: datetime | None = None,
) -> list[Puzzle]:
    """Puzzles whose review is due, most overdue first."""
    stmt = select(Puzzle).where(Puzzle.due_at <= (now or now_utc()))
    clause = _user_clause(username)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(Puzzle.due_at, Puzzle.id).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def training_summary(session: AsyncSession, username: str | None = None) -> TrainingSummary:
    """Due count and how many of the user's puzzles carry each motif. Structure studies are not
    built yet, so ``studies`` stays empty."""
    clause = _user_clause(username)
    due_stmt = select(func.count()).select_from(Puzzle).where(Puzzle.due_at <= now_utc())
    motif_stmt = (
        select(Puzzle.motif, func.count()).where(Puzzle.motif.is_not(None)).group_by(Puzzle.motif)
    )
    if clause is not None:
        due_stmt = due_stmt.where(clause)
        motif_stmt = motif_stmt.where(clause)
    due = (await session.execute(due_stmt)).scalar_one()
    rows = (await session.execute(motif_stmt)).all()
    motif_sets = sorted(
        (MotifMiss(kind=kind, count=count) for kind, count in rows),
        key=lambda m: (-m.count, m.kind),
    )
    return TrainingSummary(due_puzzles=due, motif_sets=motif_sets, studies=[])


# ---------- spaced repetition ----------


def sm2_update(puzzle: Puzzle, correct: bool, now: datetime | None = None) -> Puzzle:
    """SM-2 with a single pass/fail grade.

    Correct: reps + 1; the interval is 1 day, then 6 days, then the previous interval times
    the ease; the ease then grows by EASE_GAIN up to MAX_EASE. Wrong: the puzzle lapses, reps
    and interval reset, the ease drops by EASE_LOSS down to MIN_EASE, and it comes back in
    LAPSE_DELAY."""
    now = now or now_utc()
    reps = puzzle.reps or 0
    ease = puzzle.ease if puzzle.ease is not None else DEFAULT_EASE
    interval = puzzle.interval_days or 0.0
    if correct:
        reps += 1
        if reps <= len(FIRST_INTERVALS_DAYS):
            interval = FIRST_INTERVALS_DAYS[reps - 1]
        else:
            interval = interval * ease
        puzzle.reps = reps
        puzzle.interval_days = round(interval, 2)
        puzzle.ease = min(MAX_EASE, round(ease + EASE_GAIN, 2))
        puzzle.due_at = now + timedelta(days=puzzle.interval_days)
    else:
        puzzle.lapses = (puzzle.lapses or 0) + 1
        puzzle.reps = 0
        puzzle.interval_days = 0.0
        puzzle.ease = max(MIN_EASE, round(ease - EASE_LOSS, 2))
        puzzle.due_at = now + LAPSE_DELAY
    return puzzle


async def record_attempt(
    session: AsyncSession, puzzle: Puzzle, correct: bool, seconds: float = 0.0
) -> Puzzle:
    """Store the attempt and reschedule the puzzle."""
    now = now_utc()
    session.add(PuzzleAttempt(puzzle_id=puzzle.id, correct=correct, seconds=seconds, at=now))
    sm2_update(puzzle, correct, now)
    await session.commit()
    return puzzle


# ---------- output ----------


def to_out(puzzle: Puzzle) -> PuzzleOut:
    return PuzzleOut(
        id=puzzle.id,
        fen=puzzle.fen,
        orientation=orientation_of(puzzle.fen),
        solution=list(puzzle.solution or []),
        motif=puzzle.motif,
        source_game_id=puzzle.game_id,
        source_ply=puzzle.ply,
        due_at=puzzle.due_at,
        interval_days=puzzle.interval_days or 0.0,
        reps=puzzle.reps or 0,
    )
